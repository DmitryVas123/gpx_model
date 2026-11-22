"""Reference elevation series along a track, and comparison with the recorded
elevation (DATA_MODEL.md §5.7, ADR-012).

The reference is typically a DEM, but nothing here depends on where it came
from. A raster must not be sampled at every 1 Hz GPS point: lateral position
jitter on a slope turns into vertical noise (Nebelhorn_push: 1862 m DEM gain
at 1 Hz vs ~1440 m at 20 m spacing). So the reference is sampled at *knots*
roughly ``spacing_m`` apart along the track and interpolated linearly along
the track in between. Differences are ``recorded - reference``. Neither series
is modified.
"""

import dataclasses
from dataclasses import dataclass

import numpy as np

from alpha.domain.elevation import elevation_metrics
from alpha.domain.geodesy import (
    displacement_pairs,
    filtered_step_distances_m,
    step_distances_m,
    within_segment,
)
from alpha.domain.quality import QualityWarning, WarningCode
from alpha.domain.track import ElevationSource, FloatArray, Inference, Track

IntArray = np.ndarray[tuple[int], np.dtype[np.intp]]


def reference_knots(track: Track, spacing_m: float) -> IntArray:
    """Point indices at which to sample a reference raster.

    Per source segment: the first point, every point accepted by the
    displacement filter at ``spacing_m`` (ADR-010), and the last point.
    """
    if spacing_m <= 0:
        raise ValueError("spacing_m must be > 0")
    if track.point_count == 0:
        return np.zeros(0, dtype=np.intp)
    _, accepted = displacement_pairs(track, spacing_m)
    seg = track.source_segment
    starts = np.flatnonzero(np.concatenate(([True], seg[1:] != seg[:-1])))
    ends = np.concatenate((starts[1:] - 1, [track.point_count - 1]))
    return np.unique(np.concatenate((starts, accepted, ends))).astype(np.intp)


def interpolate_along_track(
    track: Track, knots: IntArray, knot_values: FloatArray
) -> FloatArray:
    """Linear interpolation of knot values along the (unfiltered) path length,
    per source segment. A point between two knots gets NaN if either is NaN."""
    if len(knots) != len(knot_values):
        raise ValueError("one value per knot is required")
    n = track.point_count
    result = np.full(n, np.nan)
    if n == 0:
        return result
    steps = np.where(within_segment(track), step_distances_m(track), 0.0)
    distance = np.concatenate(([0.0], np.cumsum(steps)))
    values = np.asarray(knot_values, dtype=np.float64)
    for segment in np.unique(track.source_segment):
        points = np.flatnonzero(track.source_segment == segment)
        in_segment = (knots >= points[0]) & (knots <= points[-1])
        k_idx, k_val = knots[in_segment], values[in_segment]
        if len(k_idx) == 0:
            continue
        # Knots are distinct points, but zero-length steps can repeat a distance.
        k_dist, first = np.unique(distance[k_idx], return_index=True)
        k_val = k_val[first]
        d = distance[points]
        if len(k_dist) == 1:
            result[points] = k_val[0]
            continue
        interpolated = np.interp(d, k_dist, np.nan_to_num(k_val))
        right = np.clip(np.searchsorted(k_dist, d, side="left"), 0, len(k_dist) - 1)
        left = np.clip(right - 1, 0, len(k_dist) - 1)
        exact = k_dist[right] == d
        bad = np.isnan(k_val[right]) | (~exact & np.isnan(k_val[left]))
        interpolated[bad] = np.nan
        result[points] = interpolated
    return result


@dataclass(frozen=True)
class DriftParams:
    """Detection of a slowly growing disagreement between recorded and
    reference elevation, e.g. barometer drift (ADR-014). Provisional values,
    calibrated on the reference activities."""

    bin_m: float = 100.0  # median per distance bin removes point noise
    window_m: float = 500.0  # rolling median over bins removes short excursions
    warn_range_m: float = 20.0  # smoothed difference range that raises a warning

    def __post_init__(self) -> None:
        if self.bin_m <= 0 or self.window_m < self.bin_m or self.warn_range_m <= 0:
            raise ValueError(
                "drift params: need bin_m > 0, window_m >= bin_m, warn_range_m > 0"
            )


@dataclass(frozen=True)
class ElevationDrift:
    """The smoothed difference ``recorded - reference`` along the track."""

    range_m: float  # max - min of the smoothed difference
    start_m: float  # smoothed difference at the start
    end_m: float  # ... and at the end
    min_m: float
    min_at_m: float  # filtered distance from the start
    max_m: float
    max_at_m: float
    window_m: float


def elevation_drift(
    difference_m: FloatArray, distance_m: FloatArray, params: DriftParams
) -> ElevationDrift | None:
    """Median per ``bin_m`` of distance, then a rolling median over
    ``window_m``. ``None`` with fewer than two bins of data."""
    ok = ~np.isnan(difference_m)
    if ok.sum() < 2:
        return None
    bins = np.floor(distance_m[ok] / params.bin_m).astype(np.intp)
    values = difference_m[ok]
    medians = np.full(int(bins.max()) + 1, np.nan)
    for b in np.unique(bins):
        medians[b] = np.median(values[bins == b])
    if np.count_nonzero(~np.isnan(medians)) < 2:
        return None
    half = round(params.window_m / params.bin_m) // 2
    smooth = np.full(len(medians), np.nan)
    for i in range(len(medians)):
        window = medians[max(0, i - half) : i + half + 1]
        if not np.isnan(window).all():
            smooth[i] = np.nanmedian(window)
    with_data = np.flatnonzero(~np.isnan(medians))
    smooth[np.isnan(medians)] = np.nan  # report only where there is data
    total = float(distance_m[ok].max())

    def at(i: int) -> float:
        return min((i + 0.5) * params.bin_m, total)

    i_min, i_max = int(np.nanargmin(smooth)), int(np.nanargmax(smooth))
    return ElevationDrift(
        range_m=float(smooth[i_max] - smooth[i_min]),
        start_m=float(smooth[with_data[0]]),
        end_m=float(smooth[with_data[-1]]),
        min_m=float(smooth[i_min]),
        min_at_m=at(i_min),
        max_m=float(smooth[i_max]),
        max_at_m=at(i_max),
        window_m=params.window_m,
    )


def drift_warning(
    drift: ElevationDrift | None, params: DriftParams
) -> QualityWarning | None:
    if drift is None or drift.range_m < params.warn_range_m:
        return None
    # Name the extreme farther from the start level.
    if abs(drift.min_m - drift.start_m) >= abs(drift.max_m - drift.start_m):
        far, far_at = drift.min_m, drift.min_at_m
    else:
        far, far_at = drift.max_m, drift.max_at_m
    return QualityWarning(
        WarningCode.ELEVATION_DRIFT,
        f"recorded elevation drifts against the reference: {drift.start_m:+.0f} m "
        f"at the start, {far:+.0f} m at km {far_at / 1000:.1f} "
        f"({drift.range_m:.0f} m range, {drift.window_m:g} m median windows); "
        "ascent from the recorded series is affected",
    )


@dataclass(frozen=True)
class ElevationComparison:
    compared_points: int  # points where both series have a value
    reference_coverage: float  # share of points with a reference value, 0..1
    mean_difference_m: float | None
    median_difference_m: float | None  # robust estimate of a constant offset
    std_difference_m: float | None  # shape disagreement once the offset is removed
    p95_abs_difference_m: float | None
    max_abs_difference_m: float | None
    max_abs_difference_at_m: float | None  # filtered distance from the start
    ascent_recorded_m: float | None
    descent_recorded_m: float | None
    ascent_reference_m: float | None  # same hysteresis threshold as recorded
    descent_reference_m: float | None
    drift: ElevationDrift | None = None
    warnings: tuple[QualityWarning, ...] = ()


def compare_elevation(
    track: Track,
    reference_m: FloatArray,
    ascent_threshold_m: float,
    distance_min_step_m: float,
    drift_params: DriftParams | None = None,
) -> ElevationComparison:
    if len(reference_m) != track.point_count:
        raise ValueError("reference must have one value per track point")
    reference = np.asarray(reference_m, dtype=np.float64)
    both = ~np.isnan(track.ele_raw_m) & ~np.isnan(reference)
    n = int(both.sum())
    coverage = float(np.mean(~np.isnan(reference))) if track.point_count else 0.0

    recorded = elevation_metrics(track, ascent_threshold_m)
    reference_track = with_elevation(track, reference)
    ref_metrics = elevation_metrics(reference_track, ascent_threshold_m)

    if n == 0:
        return ElevationComparison(
            0, coverage, None, None, None, None, None, None,
            recorded.ascent_m if recorded else None,
            recorded.descent_m if recorded else None,
            ref_metrics.ascent_m if ref_metrics else None,
            ref_metrics.descent_m if ref_metrics else None,
        )  # fmt: skip

    diff = track.ele_raw_m - reference
    valid = diff[both]
    abs_diff = np.where(both, np.abs(diff), -np.inf)
    worst = int(np.argmax(abs_diff))
    distance = np.concatenate(
        ([0.0], np.cumsum(filtered_step_distances_m(track, distance_min_step_m)))
    )
    dp = drift_params or DriftParams()
    drift = elevation_drift(np.where(both, diff, np.nan), distance, dp)
    warning = drift_warning(drift, dp)
    return ElevationComparison(
        compared_points=n,
        reference_coverage=coverage,
        mean_difference_m=float(valid.mean()),
        median_difference_m=float(np.median(valid)),
        std_difference_m=float(valid.std()),
        p95_abs_difference_m=float(np.percentile(np.abs(valid), 95)),
        max_abs_difference_m=float(abs_diff[worst]),
        max_abs_difference_at_m=float(distance[worst]),
        ascent_recorded_m=recorded.ascent_m if recorded else None,
        descent_recorded_m=recorded.descent_m if recorded else None,
        ascent_reference_m=ref_metrics.ascent_m if ref_metrics else None,
        descent_reference_m=ref_metrics.descent_m if ref_metrics else None,
        drift=drift,
        warnings=() if warning is None else (warning,),
    )


def with_elevation(
    track: Track,
    elevation_m: FloatArray,
    source: ElevationSource = ElevationSource.DEM_ENRICHED,
) -> Track:
    """A new Track with another elevation series; the original is unchanged."""
    return dataclasses.replace(
        track,
        ele_raw_m=elevation_m,
        elevation_source=source,
        elevation_source_inferred_by=Inference.USER,
    )
