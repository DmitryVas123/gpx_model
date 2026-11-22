"""Ascent and descent with a hysteresis threshold (DATA_MODEL.md §5.2, ADR-004)."""

from dataclasses import dataclass

import numpy as np

from alpha.domain.track import FloatArray, Track


@dataclass(frozen=True)
class ElevationMetrics:
    ascent_m: float
    descent_m: float
    min_elevation_m: float
    max_elevation_m: float


def hysteresis_ascent_descent(
    ele_m: FloatArray, threshold_m: float
) -> tuple[float, float]:
    """Total ascent and descent of one continuous elevation series.

    A climb or descent is counted from one turning point to the next, and a
    turning point is confirmed only when elevation has reversed by at least
    ``threshold_m``. Oscillations smaller than the threshold are ignored, but a
    confirmed climb is counted in full (peak to trough), not reduced by the
    threshold. With ``threshold_m == 0`` the result is the plain sum of positive
    and negative differences.

    The series must not contain NaN. ``ascent - descent`` differs from
    ``ele[-1] - ele[0]`` by less than ``2 * threshold_m``.
    """
    if threshold_m < 0:
        raise ValueError("threshold_m must be >= 0")
    if len(ele_m) < 2:
        return 0.0, 0.0
    if threshold_m == 0:
        diff = np.diff(ele_m)
        return float(diff[diff > 0].sum()), float(-diff[diff < 0].sum())

    ascent = descent = 0.0
    low = high = float(ele_m[0])
    direction = 0  # 0 = undecided, 1 = climbing, -1 = descending
    turning_point = extreme = float(ele_m[0])

    for value in ele_m[1:].tolist():
        if direction == 0:
            low, high = min(low, value), max(high, value)
            if value - low >= threshold_m:
                direction, turning_point, extreme = 1, low, value
            elif high - value >= threshold_m:
                direction, turning_point, extreme = -1, high, value
        elif direction == 1:
            if value > extreme:
                extreme = value
            elif extreme - value >= threshold_m:
                ascent += extreme - turning_point
                direction, turning_point, extreme = -1, extreme, value
        else:
            if value < extreme:
                extreme = value
            elif value - extreme >= threshold_m:
                descent += turning_point - extreme
                direction, turning_point, extreme = 1, extreme, value

    if direction == 1:
        ascent += extreme - turning_point
    elif direction == -1:
        descent += turning_point - extreme
    return ascent, descent


def elevation_metrics(track: Track, threshold_m: float) -> ElevationMetrics | None:
    """Elevation metrics per source segment, summed. ``None`` unless every point
    has elevation: partial sums would silently understate ascent."""
    if not track.has_elevation:
        return None
    ascent = descent = 0.0
    for segment in np.unique(track.source_segment):
        up, down = hysteresis_ascent_descent(
            track.ele_raw_m[track.source_segment == segment], threshold_m
        )
        ascent += up
        descent += down
    return ElevationMetrics(
        ascent_m=ascent,
        descent_m=descent,
        min_elevation_m=float(track.ele_raw_m.min()),
        max_elevation_m=float(track.ele_raw_m.max()),
    )
