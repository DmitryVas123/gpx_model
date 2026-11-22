"""Distance-based profile bins, gradient, and gradient distribution.

See DATA_MODEL.md §5.3, §5.6 and ADR-009.

The track is placed on a cumulative horizontal-distance axis that never grows
across a source-segment boundary. The axis uses the same jitter-filtered
distance as the summary (ADR-010), so bins add up to ``distance_m`` and GPS
jitter does not inflate the horizontal length of a bin (which would flatten
its gradient). Elevation and time are linearly interpolated along that axis,
separately for each source segment, so a jump between segments never appears
as a gradient or as time inside a bin.
"""

from dataclasses import dataclass

import numpy as np

from alpha.domain.geodesy import filtered_step_distances_m
from alpha.domain.track import FloatArray, Track

# A bin shorter than this share of the nominal length (only the last bin can
# be) gets no gradient: a few metres of noise would dominate it.
MIN_GRADIENT_BIN_SHARE = 0.5

# (label, lower bound inclusive, upper bound exclusive) in percent.
GRADIENT_BANDS: tuple[tuple[str, float, float], ...] = (
    ("< -25", -np.inf, -25.0),
    ("-25..-15", -25.0, -15.0),
    ("-15..-5", -15.0, -5.0),
    ("-5..5", -5.0, 5.0),
    ("5..15", 5.0, 15.0),
    ("15..25", 15.0, 25.0),
    ("25..40", 25.0, 40.0),
    (">= 40", 40.0, np.inf),
)
UNKNOWN_BAND = "unknown"


@dataclass(frozen=True)
class ProfileBin:
    index: int
    start_distance_m: float
    end_distance_m: float
    ele_start_m: float | None
    ele_end_m: float | None
    ele_change_m: float | None  # within-segment change; excludes boundary jumps
    gradient_percent: float | None
    elapsed_s: float | None  # within-segment time spent in this bin

    @property
    def length_m(self) -> float:
        return self.end_distance_m - self.start_distance_m


def cumulative_distance_m(track: Track, min_step_m: float = 0.0) -> FloatArray:
    """Horizontal distance from the first point, per point (length n).

    With ``min_step_m > 0`` the jitter-filtered distance is used: points that
    are not accepted by the filter repeat the distance of the last accepted one.
    """
    if track.point_count == 0:
        return np.zeros(0, dtype=np.float64)
    steps = filtered_step_distances_m(track, min_step_m)
    return np.concatenate(([0.0], np.cumsum(steps)))


def build_profile(
    track: Track, bin_m: float, min_step_m: float = 0.0
) -> tuple[ProfileBin, ...]:
    if bin_m <= 0:
        raise ValueError("bin_m must be > 0")
    if track.point_count < 2:
        return ()
    distance = cumulative_distance_m(track, min_step_m)
    total = float(distance[-1])
    if total <= 0:
        return ()

    edges = np.append(np.arange(0.0, total, bin_m), total)
    starts, ends = edges[:-1], edges[1:]
    n_bins = len(starts)

    with_ele = track.has_elevation
    with_time = track.has_timestamps and not np.any(np.diff(track.time_utc_s) < 0)

    ele_change = np.zeros(n_bins)
    elapsed = np.zeros(n_bins)
    ele_at_edge = np.full(len(edges), np.nan)

    for segment in np.unique(track.source_segment):
        idx = np.flatnonzero(track.source_segment == segment)
        # Zero-length steps repeat a distance value; keep the first occurrence
        # so the interpolation axis is strictly increasing.
        axis, first = np.unique(distance[idx], return_index=True)
        if len(axis) < 2:
            continue
        lo = np.maximum(starts, axis[0])
        hi = np.minimum(ends, axis[-1])
        overlap = hi > lo

        if with_ele:
            ele = track.ele_raw_m[idx][first]
            ele_change[overlap] += np.interp(hi[overlap], axis, ele) - np.interp(
                lo[overlap], axis, ele
            )
            inside = (edges >= axis[0]) & (edges <= axis[-1])
            ele_at_edge[inside] = np.interp(edges[inside], axis, ele)
        if with_time:
            time = track.time_utc_s[idx][first]
            elapsed[overlap] += np.interp(hi[overlap], axis, time) - np.interp(
                lo[overlap], axis, time
            )

    bins = []
    for k in range(n_bins):
        length = float(ends[k] - starts[k])
        gradient = None
        if with_ele and length >= MIN_GRADIENT_BIN_SHARE * bin_m:
            gradient = float(ele_change[k] / length * 100.0)
        bins.append(
            ProfileBin(
                index=k,
                start_distance_m=float(starts[k]),
                end_distance_m=float(ends[k]),
                ele_start_m=_optional(ele_at_edge[k]) if with_ele else None,
                ele_end_m=_optional(ele_at_edge[k + 1]) if with_ele else None,
                ele_change_m=float(ele_change[k]) if with_ele else None,
                gradient_percent=gradient,
                elapsed_s=float(elapsed[k]) if with_time else None,
            )
        )
    return tuple(bins)


def gradient_band(gradient_percent: float | None) -> str:
    if gradient_percent is None:
        return UNKNOWN_BAND
    for label, low, high in GRADIENT_BANDS:
        if low <= gradient_percent < high:
            return label
    raise AssertionError("bands cover the real line")  # pragma: no cover


def gradient_distribution(bins: tuple[ProfileBin, ...]) -> dict[str, float]:
    """Horizontal distance (m) per gradient band, in band order. Always contains
    every band plus ``unknown`` so distributions of different tracks align."""
    result = {label: 0.0 for label, _, _ in GRADIENT_BANDS}
    result[UNKNOWN_BAND] = 0.0
    for b in bins:
        result[gradient_band(b.gradient_percent)] += b.length_m
    return result


def _optional(value: float) -> float | None:
    return None if np.isnan(value) else float(value)
