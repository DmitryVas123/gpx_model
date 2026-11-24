"""Personal baseline from stored activities: eligibility, CLI, JSON."""

import io
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from alpha.application.baseline import personal_baseline
from alpha.application.tracks import Workspace, edit_track, import_file
from alpha.cli import EXIT_OK, main
from alpha.domain.analysis import AnalysisParams
from alpha.domain.track import TrackKind
from alpha.modelling import BaselineParams, FitStatus
from alpha.report import baseline_report
from tests.helpers import DEG_PER_METRE, LAT0, gpx_doc, trk, trkpt

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
START = datetime(2026, 6, 1, 8, 0, tzinfo=UTC)


def hike(name: str, metres_per_s: float, type_: str | None = "hiking") -> bytes:
    """1 km due north climbing 50 m, one point every 10 s, never stopping
    (under 30 min at these speeds)."""
    step_m = metres_per_s * 10
    n = round(1000 / step_m)
    points = [
        trkpt(
            LAT0 + i * step_m * DEG_PER_METRE,
            ele=500 + 50 * i / n,
            time=(START + timedelta(seconds=10 * i)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        for i in range(n + 1)
    ]
    return gpx_doc(trk(points, name=name, type_=type_))


@pytest.fixture
def ws(tmp_path: Path) -> Iterator[Workspace]:
    workspace = Workspace(tmp_path)
    yield workspace
    workspace.close()


LOOSE = BaselineParams(min_groups=2, min_moving_s=0)


def add(ws: Workspace, name: str, speed: float, type_: str | None = "hiking") -> int:
    track_id = import_file(ws, f"{name}.gpx", hike(name, speed, type_)).tracks[0].id
    edit_track(ws, track_id, kind=TrackKind.ACTIVITY)
    return track_id


def test_eligibility_is_explained(ws: Workspace) -> None:
    a = add(ws, "a", 1.0)
    b = add(ws, "b", 1.2)
    c = add(ws, "ride", 1.1, type_="cycling")
    d = add(ws, "untyped", 1.1, type_=None)
    import_file(ws, "plan.gpx", (FIXTURES / "route_only.gpx").read_bytes())

    view = personal_baseline(ws, baseline_params=LOOSE)
    assert [r.sample.track_id for r in view.fit.samples] == [a, b]
    reasons = {e.track_id: e.reason for e in view.excluded}
    assert set(reasons) == {c, d}  # the planned route is not an observation
    assert "cycling" in reasons[c] and "unknown" in reasons[d]
    assert view.fit.status is FitStatus.FITTED
    # b walked 20 % faster over the same distance and climb.
    ka, kb = (r.implied_multiplier for r in view.fit.samples)
    assert kb / ka == pytest.approx(1.2, rel=0.02)

    strict = personal_baseline(ws)  # defaults: 30 min and 5 routes
    assert strict.fit.status is FitStatus.NOT_ENOUGH_DATA
    short = {e.track_id: e.reason for e in strict.excluded}
    assert "< 30 min" in short[a] and "< 30 min" in short[b]


def test_missing_dem_series_is_an_exclusion_not_a_fallback(ws: Workspace) -> None:
    add(ws, "a", 1.0)
    view = personal_baseline(ws, AnalysisParams(elevation="dem:COP30"), LOOSE)
    assert not view.fit.samples
    assert "no COP30 elevation" in view.excluded[0].reason


def test_linked_activities_share_a_route(ws: Workspace) -> None:
    plan = import_file(ws, "plan.gpx", (FIXTURES / "route_only.gpx").read_bytes())
    plan_id = plan.tracks[0].id
    a = add(ws, "a", 1.0)
    b = add(ws, "b", 1.1)
    edit_track(ws, a, planned_track_id=plan_id)
    edit_track(ws, b, planned_track_id=plan_id)
    view = personal_baseline(ws, baseline_params=LOOSE)
    assert view.fit.n_activities == 2 and view.fit.n_groups == 1
    assert view.fit.status is FitStatus.NOT_ENOUGH_DATA


def run(*argv: str) -> tuple[int, str]:
    out = io.StringIO()
    return main(list(argv), out=out), out.getvalue()


def test_cli_and_report(tmp_path: Path) -> None:
    ws = Workspace(tmp_path)
    for i, speed in enumerate((1.0, 1.1, 1.2)):
        add(ws, f"h{i}", speed)
    view = personal_baseline(ws, baseline_params=LOOSE)
    ws.close()

    code, text = run("--data-dir", str(tmp_path), "baseline")
    assert code == EXIT_OK and "Not enough data: 0 of 5" in text  # all < 30 min
    code, text = run("--data-dir", str(tmp_path), "baseline", "--json")
    assert json.loads(text)["models"]["multiplier"]["status"] == "not enough data"
    assert len(json.loads(text)["excluded"]) == 3

    report = baseline_report(view)
    assert report["format_version"] == "10"
    assert report["n_routes"] == 3
    one = report["models"]["multiplier"]
    assert one["status"] == "fitted" and one["provenance"] == "fitted"
    assert one["assumptions"]["source"] == "fitted"
    assert one["validation"]["method"] == "leave one route out"
    two = report["models"]["split"]
    assert (
        two["status"] == "not enough data" and two["reason"] == "3 of 8 distinct routes"
    )
    assert two["horizontal_factor"] is None and two["assumptions"] is None
    assert [a["track_id"] for a in report["activities"]] == [1, 2, 3]
    assert all(a["loo_error_split"] is None for a in report["activities"])
