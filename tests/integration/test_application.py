"""Use cases against a temporary workspace (real SQLite and file store)."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from alpha.application import tracks as app
from alpha.application.tracks import (
    InvalidEditError,
    TrackNotFoundError,
    Workspace,
    compare_tracks,
    delete_source_of_track,
    edit_track,
    import_file,
    list_tracks,
    show_track,
)
from alpha.domain.analysis import AnalysisParams
from alpha.domain.track import ElevationSource, Inference, TrackKind
from alpha.gpx.reader import GpxReadError

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
ACTIVITY = (FIXTURES / "activity_two_segments.gpx").read_bytes()
ROUTE = (FIXTURES / "route_only.gpx").read_bytes()


@pytest.fixture
def ws(tmp_path: Path) -> Iterator[Workspace]:
    workspace = Workspace(tmp_path)
    yield workspace
    workspace.close()


def stored_files(ws: Workspace) -> list[Path]:
    folder = ws.root / "files"
    return sorted(folder.iterdir()) if folder.exists() else []


def test_import_stores_original_and_analysis(ws: Workspace) -> None:
    outcome = import_file(ws, "activity.gpx", ACTIVITY)
    assert outcome.created
    (record,) = outcome.tracks
    assert record.kind is TrackKind.ACTIVITY
    assert ws.files.read(outcome.source.stored_path) == ACTIVITY
    (item,) = list_tracks(ws)
    assert item.summary.distance_m > 0


def test_import_is_idempotent(ws: Workspace) -> None:
    first = import_file(ws, "a.gpx", ACTIVITY)
    second = import_file(ws, "renamed.gpx", ACTIVITY)
    assert not second.created
    assert second.source.id == first.source.id
    assert second.source.original_filename == "a.gpx"
    assert len(list_tracks(ws)) == 1
    assert len(stored_files(ws)) == 1


def test_unreadable_file_writes_nothing(ws: Workspace) -> None:
    with pytest.raises(GpxReadError):
        import_file(ws, "bad.gpx", b"not xml")
    assert list_tracks(ws) == []
    assert stored_files(ws) == []


def test_failed_transaction_removes_new_file(
    ws: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(app, "analyse", explode)
    with pytest.raises(RuntimeError):
        import_file(ws, "a.gpx", ACTIVITY)
    assert stored_files(ws) == []
    assert list_tracks(ws) == []


def test_data_survives_restart(tmp_path: Path) -> None:
    first = Workspace(tmp_path)
    import_file(first, "a.gpx", ACTIVITY)
    first.close()
    second = Workspace(tmp_path)
    try:
        (item,) = list_tracks(second)
        assert item.record.source_filename == "a.gpx"
    finally:
        second.close()


def test_show_uses_cache_and_custom_params(ws: Workspace) -> None:
    track_id = import_file(ws, "a.gpx", ACTIVITY).tracks[0].id
    default = show_track(ws, track_id)
    custom = show_track(ws, track_id, AnalysisParams(profile_bin_m=25))
    assert default.analysis.params_hash != custom.analysis.params_hash
    assert show_track(ws, track_id).analysis == default.analysis


def test_missing_track(ws: Workspace) -> None:
    with pytest.raises(TrackNotFoundError):
        show_track(ws, 42)


def test_compare_planned_with_activity(ws: Workspace) -> None:
    plan = import_file(ws, "route.gpx", ROUTE).tracks[0].id
    act = import_file(ws, "activity.gpx", ACTIVITY).tracks[0].id
    c = compare_tracks(ws, plan, act)
    assert (c.a.id, c.b.id) == (plan, act)
    moving = next(d for d in c.result.metrics if d.metric == "moving_time_s")
    assert moving.a is None and moving.b is not None


def test_link_activity_to_plan(ws: Workspace) -> None:
    plan = import_file(ws, "route.gpx", ROUTE).tracks[0].id
    act = import_file(ws, "activity.gpx", ACTIVITY).tracks[0].id
    assert edit_track(ws, act, planned_track_id=plan).planned_track_id == plan
    assert edit_track(ws, act, clear_planned_track=True).planned_track_id is None


@pytest.mark.parametrize(
    "case",
    ["self", "target-not-planned", "source-not-activity", "set-and-clear"],
)
def test_invalid_links(ws: Workspace, case: str) -> None:
    plan = import_file(ws, "route.gpx", ROUTE).tracks[0].id
    act = import_file(ws, "activity.gpx", ACTIVITY).tracks[0].id
    with pytest.raises(InvalidEditError):
        if case == "self":
            edit_track(ws, act, planned_track_id=act)
        elif case == "target-not-planned":
            edit_track(ws, plan, kind=TrackKind.ACTIVITY)
            edit_track(ws, act, planned_track_id=plan)
        elif case == "source-not-activity":
            edit_track(ws, act, kind=TrackKind.PLANNED)
            edit_track(ws, act, planned_track_id=plan)
        else:
            edit_track(ws, act, planned_track_id=plan, clear_planned_track=True)


def test_making_an_activity_planned_drops_its_link(ws: Workspace) -> None:
    plan = import_file(ws, "route.gpx", ROUTE).tracks[0].id
    act = import_file(ws, "activity.gpx", ACTIVITY).tracks[0].id
    edit_track(ws, act, planned_track_id=plan)
    assert edit_track(ws, act, kind=TrackKind.PLANNED).planned_track_id is None


def test_edit_invalidates_cached_warnings(ws: Workspace) -> None:
    act = import_file(ws, "activity.gpx", ACTIVITY).tracks[0].id
    before = {w.code for w in show_track(ws, act).analysis.warnings}
    assert "KIND_INFERRED" in before
    record = edit_track(
        ws, act, kind=TrackKind.ACTIVITY, elevation_source=ElevationSource.BAROMETRIC
    )
    assert record.kind_inferred_by is Inference.USER
    assert record.elevation_source_inferred_by is Inference.USER
    after = {w.code for w in show_track(ws, act).analysis.warnings}
    assert "KIND_INFERRED" not in after


def test_delete_requires_confirmation(ws: Workspace) -> None:
    track_id = import_file(ws, "a.gpx", ACTIVITY).tracks[0].id
    plan = delete_source_of_track(ws, track_id, confirm=False)
    assert not plan.deleted
    assert len(list_tracks(ws)) == 1
    assert len(stored_files(ws)) == 1

    done = delete_source_of_track(ws, track_id, confirm=True)
    assert done.deleted
    assert list_tracks(ws) == []
    assert stored_files(ws) == []


def test_reimport_after_delete(ws: Workspace) -> None:
    track_id = import_file(ws, "a.gpx", ACTIVITY).tracks[0].id
    delete_source_of_track(ws, track_id, confirm=True)
    assert import_file(ws, "a.gpx", ACTIVITY).created


# --- scenarios (Phase 4) ------------------------------------------------------------


def test_scenarios_for_plan_show_linked_activity(ws: Workspace) -> None:
    from alpha.application.scenarios import ObservedRelation, estimate_duration
    from alpha.domain.scenarios import DIN_33466_STANDARD

    plan = import_file(ws, "route.gpx", ROUTE).tracks[0].id
    act = import_file(ws, "activity.gpx", ACTIVITY).tracks[0].id
    assert estimate_duration(ws, plan, DIN_33466_STANDARD).observed == ()

    edit_track(ws, act, planned_track_id=plan)
    view = estimate_duration(ws, plan, DIN_33466_STANDARD)
    (observed,) = view.observed
    assert observed.track_id == act
    assert observed.relation is ObservedRelation.LINKED_ACTIVITY
    assert observed.moving_time_s is not None
    assert observed.implied_speed_multiplier is not None
    assert [s.label for s in view.result.scenarios] == ["faster", "baseline", "slower"]


def test_scenarios_for_activity_show_its_own_time(ws: Workspace) -> None:
    from alpha.application.scenarios import ObservedRelation, estimate_duration
    from alpha.domain.scenarios import DIN_33466_STANDARD

    act = import_file(ws, "activity.gpx", ACTIVITY).tracks[0].id
    (observed,) = estimate_duration(ws, act, DIN_33466_STANDARD).observed
    assert observed.relation is ObservedRelation.SAME_TRACK
