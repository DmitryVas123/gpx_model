"""Phase 3 exit criterion: compare a planned route (plan_1) with an activity
to the same summit on a different path (activity_a)."""

from pathlib import Path

import pytest

from alpha.application.tracks import Workspace, compare_tracks, edit_track, import_file
from tests.references import reference_path

ROOT = Path(__file__).resolve().parents[2]
PLAN = reference_path("plan_1")
ACTIVITY = reference_path("activity_a")


def test_plan_versus_activity(tmp_path: Path) -> None:
    if ACTIVITY is None or PLAN is None:
        pytest.skip("plan_1 or activity_a not available (personal data)")
    ws = Workspace(tmp_path)
    try:
        plan = import_file(ws, PLAN.name, PLAN.read_bytes()).tracks[0].id
        act = import_file(ws, ACTIVITY.name, ACTIVITY.read_bytes()).tracks[0].id
        assert edit_track(ws, act, planned_track_id=plan).planned_track_id == plan
        result = compare_tracks(ws, plan, act).result
    finally:
        ws.close()

    metrics = {d.metric: d for d in result.metrics}
    assert metrics["distance_m"].material is True  # 6.81 vs 8.33 km
    assert metrics["ascent_m"].material is False  # 1368 vs 1377 m
    assert metrics["moving_time_s"].a is None  # planned: unknown, not zero
    assert metrics["moving_time_s"].b is not None
    assert any(b.material for b in result.bands)
    assert result.biggest_climb_a is not None and result.biggest_climb_b is not None
    assert any("A: time metrics are unknown (planned route)" in n for n in result.notes)
