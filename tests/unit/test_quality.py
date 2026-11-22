import math

import numpy as np

from alpha.domain.quality import QualityParams, QualityWarning, WarningCode, check_track
from alpha.domain.track import ElevationSource, Inference, Track, TrackKind
from tests.helpers import LAT0, make_track

NAN = math.nan
STEP = 0.0001  # ~11.1 m of latitude


def codes(warnings: tuple[QualityWarning, ...]) -> set[WarningCode]:
    return {w.code for w in warnings}


def by_code(warnings: tuple[QualityWarning, ...], code: WarningCode) -> QualityWarning:
    return next(w for w in warnings if w.code is code)


def walk(n: int, dt: float = 10.0, ele0: float = 1000.0) -> Track:
    """Clean activity: n points, 11 m apart, every dt seconds, gentle climb."""
    return make_track(
        [LAT0 + i * STEP for i in range(n)],
        ele=[ele0 + i for i in range(n)],
        time=[i * dt for i in range(n)],
    )


def test_clean_track_has_no_warnings() -> None:
    assert check_track(walk(10)) == ()


def test_check_does_not_modify_track() -> None:
    track = walk(5)
    before = track.lat_deg.copy(), track.ele_raw_m.copy(), track.time_utc_s.copy()
    check_track(track)
    after = track.lat_deg, track.ele_raw_m, track.time_utc_s
    for b, a in zip(before, after, strict=True):
        np.testing.assert_array_equal(b, a)


def test_insufficient_points() -> None:
    for n in (0, 1):
        assert WarningCode.INSUFFICIENT_POINTS in codes(check_track(walk(n)))


def test_missing_elevation_reports_indices_and_coverage() -> None:
    track = make_track([LAT0, LAT0 + STEP, LAT0 + 2 * STEP], ele=[1.0, NAN, 3.0])
    w = by_code(check_track(track), WarningCode.MISSING_ELEVATION)
    assert w.point_indices == (1,)
    assert w.count == 1
    assert "67%" in w.message


def test_missing_timestamps_only_when_partial() -> None:
    lat = [LAT0, LAT0 + STEP, LAT0 + 2 * STEP]
    partial = make_track(lat, time=[0.0, NAN, 20.0])
    none = make_track(lat)
    assert by_code(check_track(partial), WarningCode.MISSING_TIMESTAMPS).count == 1
    assert WarningCode.MISSING_TIMESTAMPS not in codes(check_track(none))


def test_duplicate_points() -> None:
    track = make_track(
        [LAT0, LAT0, LAT0 + STEP], ele=[1.0, 1.0, 2.0], time=[0.0, 0.0, 10.0]
    )
    warnings = check_track(track)
    assert by_code(warnings, WarningCode.DUPLICATE_POINTS).point_indices == (0,)
    # An exact duplicate is not also reported as a time problem.
    assert WarningCode.NON_MONOTONIC_TIME not in codes(warnings)


def test_same_position_different_time_is_not_a_duplicate() -> None:
    track = make_track([LAT0, LAT0], ele=[1.0, 1.0], time=[0.0, 30.0])
    assert WarningCode.DUPLICATE_POINTS not in codes(check_track(track))


def test_non_monotonic_time() -> None:
    track = make_track([LAT0, LAT0 + STEP, LAT0 + 2 * STEP], time=[10.0, 5.0, 20.0])
    w = by_code(check_track(track), WarningCode.NON_MONOTONIC_TIME)
    assert w.point_indices == (0,)


def test_recording_gap() -> None:
    track = make_track([LAT0, LAT0 + STEP, LAT0 + 2 * STEP], time=[0.0, 10.0, 130.0])
    w = by_code(check_track(track), WarningCode.RECORDING_GAP)
    assert w.point_indices == (1,)
    assert "120 s" in w.message


def test_gps_jump() -> None:
    # Second step: ~111 m in 1 s.
    track = make_track(
        [LAT0, LAT0 + STEP, LAT0 + 11 * STEP, LAT0 + 12 * STEP],
        time=[0.0, 10.0, 11.0, 21.0],
    )
    w = by_code(check_track(track), WarningCode.GPS_JUMP_DETECTED)
    assert w.point_indices == (1,)


def test_gps_jump_threshold_is_a_parameter() -> None:
    track = walk(5, dt=1.0)  # ~11 m/s
    assert WarningCode.GPS_JUMP_DETECTED in codes(check_track(track))
    relaxed = QualityParams(jump_speed_m_s=20.0)
    assert WarningCode.GPS_JUMP_DETECTED not in codes(check_track(track, relaxed))


def test_elevation_spike() -> None:
    track = make_track(
        [LAT0, LAT0 + STEP, LAT0 + 2 * STEP],
        ele=[1000.0, 1050.0, 1051.0],
        time=[0.0, 10.0, 20.0],
    )
    w = by_code(check_track(track), WarningCode.ELEVATION_SPIKE_DETECTED)
    assert w.point_indices == (0,)


def test_planned_route_has_no_time_based_warnings() -> None:
    track = make_track([LAT0, LAT0 + 100 * STEP], ele=[1000.0, 1500.0], time=[NAN, NAN])
    found = codes(check_track(track))
    assert not found & {
        WarningCode.GPS_JUMP_DETECTED,
        WarningCode.ELEVATION_SPIKE_DETECTED,
        WarningCode.RECORDING_GAP,
    }


def test_segment_boundary_is_not_a_gap_or_jump() -> None:
    # 0.005 deg (~556 m) and 400 s between segments.
    track = make_track(
        [LAT0, LAT0 + STEP, LAT0 + 51 * STEP, LAT0 + 52 * STEP],
        ele=[1000.0, 1001.0, 1001.0, 1002.0],
        time=[0.0, 10.0, 410.0, 420.0],
        segment=[0, 0, 1, 1],
    )
    found = codes(check_track(track))
    assert WarningCode.SOURCE_SEGMENT_GAP in found
    assert WarningCode.RECORDING_GAP not in found
    assert WarningCode.GPS_JUMP_DETECTED not in found


def test_short_segment_boundary_is_not_reported() -> None:
    track = make_track([LAT0, LAT0 + STEP, LAT0 + 2 * STEP], segment=[0, 0, 1])
    assert WarningCode.SOURCE_SEGMENT_GAP not in codes(check_track(track))


def test_inferred_values_are_flagged() -> None:
    track = make_track(
        [LAT0, LAT0 + STEP],
        kind=TrackKind.PLANNED,
        kind_inferred_by=Inference.HEURISTIC,
        elevation_source=ElevationSource.PLANNER_DEM,
        elevation_source_inferred_by=Inference.HEURISTIC,
    )
    found = codes(check_track(track))
    assert {WarningCode.KIND_INFERRED, WarningCode.ELEVATION_SOURCE_INFERRED} <= found
