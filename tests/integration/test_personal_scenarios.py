"""Personal (fitted) speeds in duration scenarios (ADR-019)."""

import io
import json
import math
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from alpha.api import create_app
from alpha.application.baseline import (
    BaselineNotFittedError,
    personal_assumptions,
    personal_baseline,
)
from alpha.application.scenarios import estimate_personal
from alpha.application.tracks import Workspace, edit_track, import_file
from alpha.cli import EXIT_BAD_PARAMS, EXIT_INVALID, EXIT_OK, main
from alpha.domain.scenarios import AssumptionSource
from alpha.domain.track import Effort, TrackKind
from alpha.modelling import BaselineParams
from tests.integration.test_baseline import add

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
ROUTE = (FIXTURES / "route_only.gpx").read_bytes()
LOOSE = BaselineParams(min_groups=2, split_min_groups=3, min_moving_s=0)
SPEEDS = (1.0, 1.1, 1.2, 0.95, 1.05)


@pytest.fixture
def ws(tmp_path: Path) -> Iterator[Workspace]:
    workspace = Workspace(tmp_path)
    yield workspace
    workspace.close()


def five_hikes(ws: Workspace) -> list[int]:
    return [add(ws, f"h{i}", speed) for i, speed in enumerate(SPEEDS)]


def test_no_fit_is_an_error_not_a_fallback(ws: Workspace) -> None:
    track_id = add(ws, "a", 1.0)
    with pytest.raises(BaselineNotFittedError, match="alpha baseline"):
        estimate_personal(ws, track_id)  # defaults: 30 min, 5 routes


def test_personal_scenarios_use_the_fitted_speeds_and_own_range(
    ws: Workspace,
) -> None:
    ids = five_hikes(ws)
    plan = import_file(ws, "plan.gpx", ROUTE).tracks[0].id
    edit_track(ws, plan, kind=TrackKind.PLANNED)

    baseline = personal_baseline(ws, baseline_params=LOOSE)
    chosen = personal_assumptions(baseline)
    # All synthetic hikes have the same climb-to-distance mix, so the split
    # model is not identifiable: model 1 is used.
    assert chosen.model == "multiplier"
    view = estimate_personal(ws, plan, baseline_params=LOOSE, break_s=600)
    a = view.result.assumptions
    assert a.source is AssumptionSource.FITTED and a.break_s == 600
    assert a.horizontal_speed_m_h == pytest.approx(
        chosen.assumptions.horizontal_speed_m_h
    )
    assert view.personal is not None and view.personal.fitted_track_ids == tuple(ids)

    fast, base, slow = (s.speed_multiplier for s in view.result.scenarios)
    assert base == 1.0 and fast > 1.0 > slow
    k = baseline.fit.multiplier
    lo, hi = baseline.fit.single_interval or (0.0, 0.0)
    assert k is not None
    assert (fast, slow) == pytest.approx((hi / k, lo / k))  # the own 95 % range
    assert [s.label for s in view.result.scenarios] == ["faster", "baseline", "slower"]
    notes = " ".join(view.result.uncertainty_notes)
    assert "fitted from your own recorded activities" in notes
    assert "own variation" in notes and "part of the fit" not in notes
    assert "sensitivity to pace only" not in notes  # that is the preset's range

    own = estimate_personal(ws, ids[0], baseline_params=LOOSE)
    assert any("part of the fit" in n for n in own.result.uncertainty_notes)
    custom = estimate_personal(
        ws, plan, baseline_params=LOOSE, speed_multipliers=(1.0,)
    )
    assert [s.speed_multiplier for s in custom.result.scenarios] == [1.0]
    ranged = estimate_personal(
        ws, plan, baseline_params=LOOSE, speed_multipliers=(1.1, 1.0, 0.9)
    )
    ranged_notes = " ".join(ranged.result.uncertainty_notes)
    assert "sensitivity to pace only" in ranged_notes
    assert "own variation" not in ranged_notes


def test_effort_selection_reaches_the_scenario(ws: Workspace) -> None:
    ids = five_hikes(ws)
    edit_track(ws, ids[0], effort=Effort.HARD)
    normal = estimate_personal(ws, ids[1], baseline_params=LOOSE)
    assert normal.personal is not None and normal.personal.n_activities == 4
    with pytest.raises(BaselineNotFittedError):  # one hard route only
        estimate_personal(
            ws,
            ids[1],
            baseline_params=BaselineParams(min_groups=2, min_moving_s=0, effort="hard"),
        )


def run(*argv: str) -> tuple[int, str]:
    out = io.StringIO()
    return main(list(argv), out=out), out.getvalue()


def test_cli_and_api(tmp_path: Path) -> None:
    ws = Workspace(tmp_path)
    five_hikes(ws)
    ws.close()
    data = str(tmp_path)

    code, text = run("--data-dir", data, "scenarios", "1", "--personal")
    assert code == EXIT_INVALID and "no personal model is fitted yet" in text
    code, text = run(
        "--data-dir",
        data,
        "scenarios",
        "1",
        "--personal",
        "--horizontal-speed-km-h",
        "5",
    )
    assert code == EXIT_BAD_PARAMS
    code, text = run("--data-dir", data, "scenarios", "1")  # standard still works
    assert code == EXIT_OK and "DIN 33466 standard" in text

    with TestClient(create_app(tmp_path)) as client:
        missing = client.get("/tracks/1/scenarios", params={"assumptions": "personal"})
        assert (missing.status_code, missing.json()["error"]["code"]) == (
            409,
            "BASELINE_NOT_FITTED",
        )
        mixed = client.get(
            "/tracks/1/scenarios",
            params={"assumptions": "personal", "horizontal_speed_km_h": 5},
        )
        assert mixed.json()["error"]["code"] == "INVALID_PARAMS"
        standard = client.get("/tracks/1/scenarios").json()
        assert standard["personal"] is None
        baseline = client.get("/baseline").json()
        assert baseline["models"]["multiplier"]["status"] == "not enough data"
        assert len(baseline["excluded"]) == 5  # all shorter than 30 min
        assert client.get("/baseline", params={"effort": "lazy"}).status_code == 422


def test_report_carries_what_the_speeds_rest_on(ws: Workspace) -> None:
    from alpha.report import scenario_report, stored_track_ref

    five_hikes(ws)
    view = estimate_personal(ws, 1, baseline_params=LOOSE)
    r = view.record
    report = scenario_report(
        stored_track_ref(r.id, r.source_filename, r.planned_track_id),
        view.result,
        view.observed,
        view.dem,
        view.personal,
    )
    p = report["personal"]
    assert p["provenance"] == "fitted" and p["model"] == "multiplier"
    assert p["n_activities"] == 5 and p["effort"] == "normal"
    assert len(p["range_multipliers"]) == 3 and math.isclose(
        p["range_multipliers"][1], 1
    )
    assert report["assumptions"]["source"] == "fitted"
    json.dumps(report)  # serialisable
