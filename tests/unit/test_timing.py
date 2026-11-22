import math

import numpy as np
import pytest

from alpha.domain.geodesy import filtered_step_distances_m, within_segment
from alpha.domain.timing import TimeMetrics, time_metrics, windowed_speed_m_s
from alpha.domain.track import Track
from tests.helpers import DEG_PER_METRE, LAT0, line_track, make_track

GAP_S, WINDOW_S, MIN_SPEED, STOP_S = 60.0, 20.0, 0.3, 60.0


def metrics(track: Track) -> TimeMetrics | None:
    return time_metrics(
        track, filtered_step_distances_m(track, 3.0), GAP_S, WINDOW_S, MIN_SPEED, STOP_S
    )


def walk_with_pause(
    pause_s: int, *, jitter_m: float = 0.0, speed_m_s: float = 1.0
) -> Track:
    """100 s walking north, ``pause_s`` standing still, 100 s walking."""
    rng = np.random.default_rng(0)
    pos = list(np.arange(100.0) * speed_m_s)
    stand = pos[-1] + speed_m_s
    pos += list(stand + rng.uniform(-jitter_m, jitter_m, pause_s))
    pos += list(stand + np.arange(1, 101.0) * speed_m_s)
    lat = LAT0 + np.array(pos) * DEG_PER_METRE
    return make_track(lat, ele=[1000.0] * len(pos), time=np.arange(len(pos)))


def assert_identity(m: TimeMetrics) -> None:
    assert m.elapsed_time_s == pytest.approx(
        m.moving_time_s + m.stopped_time_s + m.recording_gap_s
    )


# --- unknown --------------------------------------------------------------------


def test_planned_route_time_is_unknown() -> None:
    assert metrics(line_track(10.0, 500.0)) is None


def test_partial_timestamps_are_unknown() -> None:
    track = make_track([LAT0, LAT0, LAT0], time=[0.0, math.nan, 2.0])
    assert metrics(track) is None


def test_time_running_backwards_is_unknown() -> None:
    track = make_track([LAT0, LAT0, LAT0], time=[0.0, 10.0, 5.0])
    assert metrics(track) is None


# --- windowed speed ---------------------------------------------------------------


def test_windowed_speed_of_steady_walk() -> None:
    track = line_track(1.0, 200.0, lambda d: d * 0, seconds_per_point=1.0)
    gap = ~within_segment(track)
    speed = windowed_speed_m_s(track, gap, 20.0)
    assert speed[50:150] == pytest.approx(1.0, rel=0.02)


def test_windowed_speed_does_not_depend_on_sampling_rate() -> None:
    fast = line_track(1.0, 300.0, lambda d: d * 0, seconds_per_point=1.0)
    slow = line_track(5.0, 300.0, lambda d: d * 0, seconds_per_point=5.0)
    s_fast = windowed_speed_m_s(fast, ~within_segment(fast), 20.0)
    s_slow = windowed_speed_m_s(slow, ~within_segment(slow), 20.0)
    assert s_fast[150] == pytest.approx(s_slow[30], rel=0.02)


def test_windowed_speed_stops_at_gaps() -> None:
    lat = LAT0 + np.array([0.0, 1.0, 1000.0, 1001.0]) * DEG_PER_METRE
    track = make_track(lat, time=[0.0, 1.0, 2.0, 3.0])
    gap = np.array([False, True, False])
    speed = windowed_speed_m_s(track, gap, 20.0)
    assert speed.max() == pytest.approx(1.0, rel=0.02)


def test_invalid_window() -> None:
    track = line_track(1.0, 10.0, seconds_per_point=1.0)
    with pytest.raises(ValueError):
        windowed_speed_m_s(track, ~within_segment(track), 0.0)


# --- classification ----------------------------------------------------------------


def test_continuous_walk_is_all_moving() -> None:
    m = metrics(line_track(1.0, 500.0, lambda d: d * 0, seconds_per_point=1.0))
    assert m is not None
    assert m.moving_time_s == m.elapsed_time_s == 500.0
    assert (m.stopped_time_s, m.recording_gap_s, m.stop_count) == (0.0, 0.0, 0)


def test_pause_is_stopped_time() -> None:
    m = metrics(walk_with_pause(120))
    assert m is not None
    assert m.stop_count == 1
    # The centred window blurs each edge of the pause by under half a window.
    assert 120.0 - WINDOW_S <= m.stopped_time_s <= 120.0
    assert_identity(m)


def test_short_pause_is_stopped_but_not_a_stop() -> None:
    m = metrics(walk_with_pause(40))
    assert m is not None
    assert m.stop_count == 0  # shorter than STOP_S
    assert 0 < m.stopped_time_s <= 40.0


def test_jitter_while_standing_is_stopped_and_mostly_filtered() -> None:
    m = metrics(walk_with_pause(600, jitter_m=2.0))
    assert m is not None
    assert m.stop_count == 1
    assert m.stopped_time_s == pytest.approx(600.0, abs=WINDOW_S)
    # Raw path length of 600 s of +-2 m jitter would be hundreds of metres.
    assert m.distance_while_stopped_m < 30.0


def test_slow_walking_counts_as_stopped() -> None:
    # Documented limitation (ADR-007): below the speed threshold is "stopped".
    m = metrics(line_track(0.2, 40.0, lambda d: d * 0, seconds_per_point=1.0))
    assert m is not None
    assert m.moving_time_s == 0.0


def test_slow_steep_climb_is_moving() -> None:
    # 0.05 m/s horizontally but 0.4 m/s vertically: 3D speed is above 0.3 m/s.
    n = 200
    lat = LAT0 + np.arange(n) * 0.05 * DEG_PER_METRE
    track = make_track(lat, ele=1000 + 0.4 * np.arange(n), time=np.arange(n))
    m = metrics(track)
    assert m is not None
    assert m.moving_time_s == pytest.approx(m.elapsed_time_s)


def test_recording_gap() -> None:
    lat = LAT0 + np.arange(4) * 10 * DEG_PER_METRE
    m = metrics(make_track(lat, time=[0.0, 10.0, 310.0, 320.0]))
    assert m is not None
    assert m.recording_gap_s == 300.0
    assert m.moving_time_s == 20.0
    assert_identity(m)


def test_time_between_source_segments_is_a_gap() -> None:
    lat = LAT0 + np.arange(4) * 10 * DEG_PER_METRE
    m = metrics(make_track(lat, time=[0.0, 10.0, 40.0, 50.0], segment=[0, 0, 1, 1]))
    assert m is not None
    assert m.recording_gap_s == 30.0
    assert m.moving_time_s == 20.0


@pytest.mark.parametrize("pause", [0, 45, 61, 300])
@pytest.mark.parametrize("speed", [0.5, 1.0, 1.5])
def test_time_identity(pause: int, speed: float) -> None:
    m = metrics(walk_with_pause(pause, jitter_m=1.0, speed_m_s=speed))
    assert m is not None
    assert_identity(m)


def test_faster_threshold_never_increases_moving_time() -> None:
    track = walk_with_pause(120, jitter_m=2.0, speed_m_s=0.6)
    dist = filtered_step_distances_m(track, 3.0)
    moving = [
        time_metrics(track, dist, GAP_S, WINDOW_S, v, STOP_S).moving_time_s  # type: ignore[union-attr]
        for v in (0.1, 0.3, 0.5, 0.7)
    ]
    assert moving == sorted(moving, reverse=True)
