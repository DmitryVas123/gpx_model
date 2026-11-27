"""Phase 4 exit criterion: a planned route with a linked activity shows the
recorded moving time next to the estimated scenarios, labelled observed."""

from pathlib import Path

import pytest

from alpha.application.scenarios import ObservedRelation, estimate_duration
from alpha.application.tracks import Workspace, edit_track, import_file
from alpha.domain.scenarios import DIN_33466_STANDARD
from tests.references import reference_path

ROOT = Path(__file__).resolve().parents[2]
PLAN = reference_path("plan_1")
ACTIVITY = reference_path("activity_a")


def test_plan_estimate_next_to_observed_activity(tmp_path: Path) -> None:
    if PLAN is None:
        pytest.skip("plan_1 not available (personal data)")
    ws = Workspace(tmp_path)
    try:
        plan = import_file(ws, PLAN.name, PLAN.read_bytes()).tracks[0].id
        # DIN 33466 standard on 6.81 km / 1368 m up / 0 m down:
        # vertical 4.56 h dominates, plus half of 1.70 h horizontal = 5.41 h.
        baseline = estimate_duration(ws, plan, DIN_33466_STANDARD).result.scenarios[1]
        assert baseline.label == "baseline"
        assert baseline.breakdown.moving_s == pytest.approx(5.41 * 3600, abs=60)

        if ACTIVITY is None:
            pytest.skip("activity_a not available (personal data)")
        act = import_file(ws, ACTIVITY.name, ACTIVITY.read_bytes()).tracks[0].id
        edit_track(ws, act, planned_track_id=plan)
        (observed,) = estimate_duration(ws, plan, DIN_33466_STANDARD).observed
    finally:
        ws.close()

    assert observed.relation is ObservedRelation.LINKED_ACTIVITY
    assert observed.moving_time_s == pytest.approx(2.49 * 3600, abs=120)
    # DIN on the activity's own 8.33 km / 1377 m is ~5.67 h -> about x2.28.
    assert observed.implied_speed_multiplier == pytest.approx(2.28, abs=0.03)
