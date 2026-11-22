"""Data-quality checks (DATA_MODEL.md §8).

Checks never modify or drop data; they only report. Point indices refer to the
track arrays. For step-based warnings the index is the first point of the step.
"""

from dataclasses import dataclass
from enum import StrEnum

import numpy as np
import numpy.typing as npt

from alpha.domain.geodesy import step_distances_m, within_segment
from alpha.domain.track import Inference, Track


class WarningCode(StrEnum):
    NO_TRACKS = "NO_TRACKS"
    INSUFFICIENT_POINTS = "INSUFFICIENT_POINTS"
    INVALID_COORDINATE = "INVALID_COORDINATE"
    MISSING_ELEVATION = "MISSING_ELEVATION"
    MISSING_TIMESTAMPS = "MISSING_TIMESTAMPS"
    NON_MONOTONIC_TIME = "NON_MONOTONIC_TIME"
    DUPLICATE_POINTS = "DUPLICATE_POINTS"
    RECORDING_GAP = "RECORDING_GAP"
    GPS_JUMP_DETECTED = "GPS_JUMP_DETECTED"
    ELEVATION_SPIKE_DETECTED = "ELEVATION_SPIKE_DETECTED"
    SOURCE_SEGMENT_GAP = "SOURCE_SEGMENT_GAP"
    KIND_INFERRED = "KIND_INFERRED"
    ELEVATION_SOURCE_INFERRED = "ELEVATION_SOURCE_INFERRED"
    GRADIENT_OUTLIER = "GRADIENT_OUTLIER"
    ELEVATION_DRIFT = "ELEVATION_DRIFT"


@dataclass(frozen=True)
class QualityWarning:
    code: WarningCode
    message: str
    point_indices: tuple[int, ...] = ()
    count: int | None = None
    bin_indices: tuple[int, ...] = ()  # profile bins (analysis warnings only)


@dataclass(frozen=True)
class QualityParams:
    """Provisional thresholds; see DATA_MODEL.md §8 and the referenced ADRs."""

    gap_threshold_s: float = 60.0
    jump_speed_m_s: float = 5.0  # hiking; faster steps are flagged
    max_vertical_speed_m_s: float = 3.0  # reference activities peak at 1.4 m/s
    source_segment_gap_m: float = 100.0


BoolArray = npt.NDArray[np.bool_]


def _indices(mask: BoolArray) -> tuple[int, ...]:
    return tuple(int(i) for i in np.flatnonzero(mask))


def _nan_equal(a: npt.NDArray[np.float64], b: npt.NDArray[np.float64]) -> BoolArray:
    return np.asarray((a == b) | (np.isnan(a) & np.isnan(b)))


def check_track(
    track: Track, params: QualityParams | None = None
) -> tuple[QualityWarning, ...]:
    p = params or QualityParams()
    warnings: list[QualityWarning] = []
    n = track.point_count

    if n < 2:
        warnings.append(
            QualityWarning(
                WarningCode.INSUFFICIENT_POINTS,
                f"Track has {n} point(s); at least 2 are needed for any metric.",
                count=n,
            )
        )

    warnings.extend(_inference_warnings(track))

    if n == 0:
        return tuple(warnings)

    missing_ele = np.isnan(track.ele_raw_m)
    if missing_ele.any():
        warnings.append(
            QualityWarning(
                WarningCode.MISSING_ELEVATION,
                f"{int(missing_ele.sum())} of {n} points have no elevation "
                f"(coverage {track.elevation_coverage:.0%}).",
                _indices(missing_ele),
                int(missing_ele.sum()),
            )
        )

    missing_time = np.isnan(track.time_utc_s)
    if missing_time.any() and not missing_time.all():
        warnings.append(
            QualityWarning(
                WarningCode.MISSING_TIMESTAMPS,
                f"{int(missing_time.sum())} of {n} points have no timestamp; "
                "time metrics will be unknown.",
                _indices(missing_time),
                int(missing_time.sum()),
            )
        )

    if n < 2:
        return tuple(warnings)

    warnings.extend(_step_warnings(track, p))
    return tuple(warnings)


def _inference_warnings(track: Track) -> list[QualityWarning]:
    result = []
    if track.kind_inferred_by is Inference.HEURISTIC:
        result.append(
            QualityWarning(
                WarningCode.KIND_INFERRED,
                f"Track kind '{track.kind}' was guessed from the data; "
                "confirm or correct it.",
            )
        )
    if track.elevation_source_inferred_by is Inference.HEURISTIC:
        result.append(
            QualityWarning(
                WarningCode.ELEVATION_SOURCE_INFERRED,
                f"Elevation source '{track.elevation_source}' was guessed from the "
                "data; confirm or correct it.",
            )
        )
    return result


def _step_warnings(track: Track, p: QualityParams) -> list[QualityWarning]:
    result: list[QualityWarning] = []
    lat, lon = track.lat_deg, track.lon_deg
    ele, time = track.ele_raw_m, track.time_utc_s

    same_segment = within_segment(track)
    distance = step_distances_m(track)
    dt = np.diff(time)  # NaN where either timestamp is missing
    has_dt = ~np.isnan(dt)

    duplicate = (
        same_segment
        & (lat[1:] == lat[:-1])
        & (lon[1:] == lon[:-1])
        & _nan_equal(ele[1:], ele[:-1])
        & _nan_equal(time[1:], time[:-1])
    )
    if duplicate.any():
        result.append(
            QualityWarning(
                WarningCode.DUPLICATE_POINTS,
                f"{int(duplicate.sum())} point(s) repeat the previous point exactly.",
                _indices(duplicate),
                int(duplicate.sum()),
            )
        )

    with np.errstate(invalid="ignore"):
        non_monotonic = has_dt & (dt <= 0) & ~duplicate
    if non_monotonic.any():
        result.append(
            QualityWarning(
                WarningCode.NON_MONOTONIC_TIME,
                f"{int(non_monotonic.sum())} step(s) have a timestamp not later "
                "than the previous point.",
                _indices(non_monotonic),
                int(non_monotonic.sum()),
            )
        )

    with np.errstate(invalid="ignore"):
        gap = same_segment & has_dt & (dt > p.gap_threshold_s)
    if gap.any():
        result.append(
            QualityWarning(
                WarningCode.RECORDING_GAP,
                f"{int(gap.sum())} step(s) longer than {p.gap_threshold_s:g} s "
                f"(total {float(dt[gap].sum()):.0f} s); recording paused or "
                "signal lost.",
                _indices(gap),
                int(gap.sum()),
            )
        )

    timed = same_segment & has_dt & (np.nan_to_num(dt) > 0)
    safe_dt = np.where(timed, dt, 1.0)

    jump = timed & (distance / safe_dt > p.jump_speed_m_s)
    if jump.any():
        result.append(
            QualityWarning(
                WarningCode.GPS_JUMP_DETECTED,
                f"{int(jump.sum())} step(s) faster than {p.jump_speed_m_s:g} m/s, "
                f"covering {float(distance[jump].sum()):.0f} m; possible GPS jumps "
                "or motorised transport.",
                _indices(jump),
                int(jump.sum()),
            )
        )

    de = np.diff(ele)
    with np.errstate(invalid="ignore"):
        spike = (
            timed & ~np.isnan(de) & (np.abs(de) / safe_dt > p.max_vertical_speed_m_s)
        )
    if spike.any():
        result.append(
            QualityWarning(
                WarningCode.ELEVATION_SPIKE_DETECTED,
                f"{int(spike.sum())} step(s) change elevation faster than "
                f"{p.max_vertical_speed_m_s:g} m/s.",
                _indices(spike),
                int(spike.sum()),
            )
        )

    segment_gap = ~same_segment & (distance > p.source_segment_gap_m)
    if segment_gap.any():
        result.append(
            QualityWarning(
                WarningCode.SOURCE_SEGMENT_GAP,
                f"{int(segment_gap.sum())} source-segment boundary(ies) span more "
                f"than {p.source_segment_gap_m:g} m; this distance is not counted.",
                _indices(segment_gap),
                int(segment_gap.sum()),
            )
        )

    return result
