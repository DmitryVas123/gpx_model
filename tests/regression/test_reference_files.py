"""Phase 1 exit criteria on the reference files (DATA_MODEL.md §3).

Personal activities live in data/local/ (git-ignored); their tests are skipped
when the files are absent, e.g. on a fresh clone.
"""

import pytest

from alpha.domain.quality import WarningCode, check_track
from alpha.domain.track import ElevationSource, TrackKind
from alpha.gpx.reader import read_gpx
from tests.references import reference


@pytest.mark.parametrize(
    ("key", "points"),
    [("plan_1", 531), ("plan_2", 374)],
)
def test_planned_routes(key: str, points: int) -> None:
    doc = read_gpx(reference(key).read_bytes())
    (source,) = doc.tracks
    t = source.track
    assert t.point_count == points
    assert t.kind is TrackKind.PLANNED
    assert t.elevation_source is ElevationSource.PLANNER_DEM
    assert t.has_elevation
    assert not t.has_timestamps
    assert source.read_warnings == ()


@pytest.mark.parametrize(
    ("key", "points", "gaps", "jumps"),
    [
        ("activity_a", 13_473, 1, 3),
        ("activity_b", 24_827, 3, 14),
    ],
)
def test_recorded_activities(key: str, points: int, gaps: int, jumps: int) -> None:
    doc = read_gpx(reference(key).read_bytes())
    (source,) = doc.tracks
    t = source.track
    assert t.point_count == points
    assert t.kind is TrackKind.ACTIVITY
    assert t.has_timestamps
    assert t.has_elevation
    assert t.activity_type == "hiking"

    warnings = {w.code: w for w in check_track(t)}
    assert warnings[WarningCode.RECORDING_GAP].count == gaps
    assert warnings[WarningCode.GPS_JUMP_DETECTED].count == jumps
    assert WarningCode.ELEVATION_SPIKE_DETECTED not in warnings
    assert WarningCode.NON_MONOTONIC_TIME not in warnings
