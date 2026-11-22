"""Elapsed, moving, stopped, and gap time for activities (DATA_MODEL.md §5.4, ADR-007).

Identity: ``elapsed = moving + stopped + recording_gap`` (exactly, up to float
rounding), so every second of an activity is assigned to one category.
"""

from dataclasses import dataclass

import numpy as np

from alpha.domain.geodesy import BoolArray, local_xyz_m, within_segment
from alpha.domain.track import FloatArray, Track


@dataclass(frozen=True)
class TimeMetrics:
    elapsed_time_s: float
    moving_time_s: float
    stopped_time_s: float
    recording_gap_s: float
    stop_count: int  # stopped runs lasting at least stop_min_duration_s
    distance_while_stopped_m: float  # filtered distance credited while stopped


def time_metrics(
    track: Track,
    step_distance_m: FloatArray,
    gap_threshold_s: float,
    window_s: float,
    min_speed_m_s: float,
    stop_min_duration_s: float,
) -> TimeMetrics | None:
    """``None`` (unknown) unless every point has a timestamp and time never runs
    backwards. Planned routes therefore get ``None``, never zero.

    ``step_distance_m`` (length n-1) is the distance credited to each step,
    normally the jitter-filtered distance; it is only used to report
    ``distance_while_stopped_m``.
    """
    if not track.has_timestamps or track.point_count < 2:
        return None
    dt = np.diff(track.time_utc_s)
    if np.any(dt < 0):
        return None

    gap = ~within_segment(track) | (dt > gap_threshold_s)
    speed = windowed_speed_m_s(track, gap, window_s)
    moving = ~gap & (speed[:-1] > min_speed_m_s)
    stopped = ~gap & ~moving

    return TimeMetrics(
        elapsed_time_s=float(track.time_utc_s[-1] - track.time_utc_s[0]),
        moving_time_s=float(dt[moving].sum()),
        stopped_time_s=float(dt[stopped].sum()),
        recording_gap_s=float(dt[gap].sum()),
        stop_count=_count_runs(stopped, dt, stop_min_duration_s),
        distance_while_stopped_m=float(step_distance_m[stopped].sum()),
    )


def windowed_speed_m_s(track: Track, gap: BoolArray, window_s: float) -> FloatArray:
    """Speed at each point: 3D displacement across a centred time window.

    The window spans ``window_s`` seconds of timestamps (not a number of
    points), so the result does not depend on the sampling rate. It never
    reaches across a recording gap or source-segment boundary (``gap``, length
    n-1). Using displacement rather than summed step lengths makes GPS jitter
    while standing still average out; using 3D keeps slow but steep climbing
    above the threshold.
    """
    if window_s <= 0:
        raise ValueError("window_s must be > 0")
    n = track.point_count
    speed = np.zeros(n, dtype=np.float64)
    x, y, z = local_xyz_m(track)
    t = track.time_utc_s
    run_starts = np.concatenate(([0], np.flatnonzero(gap) + 1))
    run_ends = np.concatenate((np.flatnonzero(gap) + 1, [n]))
    half = window_s / 2.0

    for start, end in zip(run_starts.tolist(), run_ends.tolist(), strict=True):
        tr = t[start:end]
        i0 = start + np.searchsorted(tr, tr - half, side="left")
        i1 = start + np.searchsorted(tr, tr + half, side="right") - 1
        span = t[i1] - t[i0]
        displacement = np.sqrt(
            (x[i1] - x[i0]) ** 2 + (y[i1] - y[i0]) ** 2 + (z[i1] - z[i0]) ** 2
        )
        speed[start:end] = np.divide(
            displacement, span, out=np.zeros_like(span), where=span > 0
        )
    return speed


def _count_runs(mask: BoolArray, dt: FloatArray, min_duration_s: float) -> int:
    """Count maximal runs of True in ``mask`` lasting at least ``min_duration_s``."""
    count = 0
    duration = 0.0
    for flag, seconds in zip(mask.tolist(), dt.tolist(), strict=True):
        if flag:
            duration += seconds
        else:
            count += duration >= min_duration_s and duration > 0
            duration = 0.0
    count += duration >= min_duration_s and duration > 0
    return count
