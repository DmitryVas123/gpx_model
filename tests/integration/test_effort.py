"""Effort annotation (ADR-018): storage, edit rules, API, baseline selection."""

import io
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from alpha.api import create_app
from alpha.application.baseline import personal_baseline
from alpha.application.tracks import (
    InvalidEditError,
    Workspace,
    delete_source_of_track,
    edit_track,
    import_file,
)
from alpha.cli import EXIT_OK, main
from alpha.domain.track import Effort, TrackKind
from alpha.modelling import BaselineParams
from alpha.persistence import Database
from alpha.persistence.schema import SCHEMA_VERSION
from alpha.report import baseline_report, record_report
from tests.integration.test_baseline import add

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
ROUTE = (FIXTURES / "route_only.gpx").read_bytes()
LOOSE = BaselineParams(min_groups=2, split_min_groups=3, min_moving_s=0)


@pytest.fixture
def ws(tmp_path: Path) -> Iterator[Workspace]:
    workspace = Workspace(tmp_path)
    yield workspace
    workspace.close()


def test_effort_is_stored_cleared_and_reported(ws: Workspace) -> None:
    track_id = add(ws, "a", 1.0)
    assert edit_track(ws, track_id, effort=Effort.HARD).effort is Effort.HARD
    assert (
        record_report(edit_track(ws, track_id, kind=TrackKind.ACTIVITY))["effort"]
        == "hard"
    )  # other edits keep it
    assert edit_track(ws, track_id, clear_effort=True).effort is None
    with pytest.raises(InvalidEditError, match="at once"):
        edit_track(ws, track_id, effort=Effort.EASY, clear_effort=True)

    plan = import_file(ws, "plan.gpx", ROUTE).tracks[0].id
    edit_track(ws, plan, kind=TrackKind.PLANNED)
    with pytest.raises(InvalidEditError, match="activities"):
        edit_track(ws, plan, effort=Effort.HARD)

    edit_track(ws, track_id, effort=Effort.EASY)
    delete_source_of_track(ws, track_id, confirm=True)  # annotation cascades
    with ws.db.transaction() as repo:
        assert repo.get_track(track_id) is None


def test_version_3_database_gains_the_annotation_table(tmp_path: Path) -> None:
    path = tmp_path / "alpha.db"
    Database(path).close()
    connection = sqlite3.connect(path)
    connection.execute("drop table track_annotations")
    connection.execute("update meta set value = '3'")
    connection.commit()
    connection.close()
    Database(path).close()
    connection = sqlite3.connect(path)
    version = connection.execute("select value from meta").fetchone()[0]
    tables = {r[0] for r in connection.execute("select name from sqlite_master")}
    connection.close()
    assert version == str(SCHEMA_VERSION) and "track_annotations" in tables


def test_baseline_fits_normal_efforts_and_compares_the_rest(ws: Workspace) -> None:
    normal = [add(ws, f"n{i}", speed) for i, speed in enumerate((1.0, 1.05, 1.1))]
    hard = add(ws, "push", 1.5)
    edit_track(ws, hard, effort=Effort.HARD)
    edit_track(ws, normal[0], effort=Effort.NORMAL)

    view = personal_baseline(ws, baseline_params=LOOSE)
    assert [r.sample.track_id for r in view.fit.samples] == normal
    reason = {e.track_id: e.reason for e in view.excluded}[hard]
    assert "effort hard" in reason
    (outside,) = view.outside_effort
    assert outside.sample.track_id == hard
    # 1.5 m/s against a model fitted on about 1.05 m/s: clearly faster.
    assert outside.vs_multiplier is not None and outside.vs_multiplier > 1.3
    groups = {g.effort: g for g in view.effort_groups}
    assert groups[Effort.HARD].n_activities == 1
    assert groups[Effort.NORMAL].n_activities == 1 and groups[None].n_activities == 2
    assert (
        groups[Effort.HARD].mean_implied_multiplier
        > groups[None].mean_implied_multiplier
    )

    report = baseline_report(view)
    assert report["format_version"] == "10"
    assert report["effort"]["fitted_on"] == "normal"
    assert report["effort"]["included"] == ["normal", "not stated"]
    assert report["effort"]["outside_fit"][0]["effort"] == "hard"

    everything = personal_baseline(
        ws, baseline_params=BaselineParams(min_groups=2, min_moving_s=0, effort="all")
    )
    assert everything.fit.n_activities == 4 and not everything.outside_effort
    only_hard = personal_baseline(
        ws, baseline_params=BaselineParams(min_groups=2, min_moving_s=0, effort="hard")
    )
    assert [r.sample.track_id for r in only_hard.fit.samples] == [hard]
    with pytest.raises(ValueError, match="effort must be"):
        BaselineParams(effort="lazy")


def test_cli_and_api(tmp_path: Path) -> None:
    out = io.StringIO()
    ws = Workspace(tmp_path)
    track_id = add(ws, "a", 1.0)
    ws.close()
    assert (
        main(
            ["--data-dir", str(tmp_path), "edit", str(track_id), "--effort", "hard"],
            out=out,
        )
        == EXIT_OK
    )
    assert "[hard effort]" in out.getvalue()
    out = io.StringIO()
    main(
        ["--data-dir", str(tmp_path), "baseline", "--effort", "all", "--json"], out=out
    )
    assert json.loads(out.getvalue())["effort"]["fitted_on"] == "all"

    with TestClient(create_app(tmp_path)) as client:
        assert client.get("/tracks").json()[0]["record"]["effort"] == "hard"
        cleared = client.patch(f"/tracks/{track_id}", json={"clear_effort": True})
        assert cleared.json()["effort"] is None
        easy = client.patch(f"/tracks/{track_id}", json={"effort": "easy"})
        assert easy.json()["effort"] == "easy"
        bad = client.patch(f"/tracks/{track_id}", json={"effort": "lazy"})
        assert bad.status_code == 422
