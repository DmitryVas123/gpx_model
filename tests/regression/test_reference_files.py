"""Phase 1 exit criteria on the reference files (DATA_MODEL.md §3).

Personal activities live in data/local/ (git-ignored); their tests are skipped
when the files are absent, e.g. on a fresh clone.
"""

from pathlib import Path

import pytest

from alpha.domain.quality import WarningCode, check_track
from alpha.domain.track import ElevationSource, TrackKind
from alpha.gpx.reader import read_gpx

DATA = Path(__file__).resolve().parents[2] / "data"


def load(relative: str) -> Path:
    path = DATA / relative
    if not path.exists():
        pytest.skip(f"{relative} not present (personal data is not in Git)")
    return path


@pytest.mark.parametrize(
    ("relative", "points"),
    [("samples/input.gpx", 531), ("samples/input2.gpx", 374)],
)
def test_planned_routes(relative: str, points: int) -> None:
    doc = read_gpx(load(relative).read_bytes())
    (source,) = doc.tracks
    t = source.track
    assert t.point_count == points
    assert t.kind is TrackKind.PLANNED
    assert t.elevation_source is ElevationSource.PLANNER_DEM
    assert t.has_elevation
    assert not t.has_timestamps
    assert source.read_warnings == ()


@pytest.mark.parametrize(
    ("relative", "points", "gaps", "jumps"),
    [
        ("local/Nebelhorn_push.gpx", 13_473, 1, 3),
        ("local/Gleitweg_Seealpsee_Zeigersattel.gpx", 24_827, 3, 14),
    ],
)
def test_recorded_activities(relative: str, points: int, gaps: int, jumps: int) -> None:
    doc = read_gpx(load(relative).read_bytes())
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
