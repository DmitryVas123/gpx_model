"""Deterministic, versioned track analysis (DATA_MODEL.md §6)."""

import hashlib
import json
from dataclasses import asdict, dataclass, field

import numpy as np

from alpha.domain.climbs import ClimbSection, find_climb_sections
from alpha.domain.elevation import elevation_metrics
from alpha.domain.geodesy import (
    displacement_pairs,
    filtered_step_distances_m,
    step_distances_m,
    within_segment,
)
from alpha.domain.profile import ProfileBin, build_profile, gradient_distribution
from alpha.domain.quality import (
    QualityParams,
    QualityWarning,
    WarningCode,
    check_track,
)
from alpha.domain.timing import time_metrics
from alpha.domain.track import ElevationSource, Track

# Bump when any calculation changes its output for the same input and params.
# Regression tests on the reference files make such changes visible.
# 0.2.0: jitter-filtered distance (ADR-010) and windowed-speed moving time
#        (ADR-007 rev. 2), both calibrated against Strava on two activities.
# 0.3.0: elevation series is an explicit parameter (ADR-012); results with
#        recorded elevation are numerically unchanged.
ALGORITHM_VERSION = "0.3.0"

ELEVATION_RECORDED = "recorded"
DEM_PREFIX = "dem:"


def dem_dataset_of(elevation: str) -> str | None:
    """Dataset id for ``"dem:<id>"``; ``None`` for recorded elevation."""
    if elevation == ELEVATION_RECORDED:
        return None
    if elevation.startswith(DEM_PREFIX) and len(elevation) > len(DEM_PREFIX):
        return elevation[len(DEM_PREFIX) :]
    raise ValueError(
        f"elevation must be '{ELEVATION_RECORDED}' or '{DEM_PREFIX}<dataset>', "
        f"not {elevation!r}"
    )


@dataclass(frozen=True)
class AnalysisParams:
    """Every tunable value of an analysis. Defaults are provisional; see the ADRs."""

    quality: QualityParams = field(default_factory=QualityParams)
    profile_bin_m: float = 50.0  # ADR-009
    ascent_threshold_m: float = 3.0  # ADR-004
    gradient_outlier_percent: float = 100.0  # ADR-009
    distance_min_step_m: float = 3.0  # ADR-010
    moving_window_s: float = 20.0  # ADR-007
    moving_min_speed_m_s: float = 0.3  # ADR-007
    stop_min_duration_s: float = 60.0  # ADR-007; only for stop_count
    climb_min_gradient_percent: float = 8.0
    climb_max_interruption_m: float = 150.0
    climb_min_elevation_change_m: float = 50.0
    # Which elevation series every elevation-dependent metric uses: the
    # recorded one, or a stored DEM profile such as "dem:COP30" (ADR-012).
    elevation: str = ELEVATION_RECORDED

    def __post_init__(self) -> None:
        positive = (
            "profile_bin_m",
            "moving_window_s",
            "climb_min_gradient_percent",
        )
        for name in positive:
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be > 0")
        non_negative = (
            "ascent_threshold_m",
            "gradient_outlier_percent",
            "distance_min_step_m",
            "moving_min_speed_m_s",
            "stop_min_duration_s",
            "climb_max_interruption_m",
            "climb_min_elevation_change_m",
        )
        for name in non_negative:
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be >= 0")
        dem_dataset_of(self.elevation)  # validates the format


def params_hash(params: AnalysisParams) -> str:
    """SHA-256 of the canonical JSON of the parameters."""
    canonical = json.dumps(asdict(params), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class TrackSummary:
    """``None`` means unknown (e.g. time metrics of a planned route), never zero."""

    distance_m: float  # jitter-filtered (ADR-010)
    distance_raw_m: float  # every within-segment step, unfiltered
    distance_3d_m: float | None
    ascent_m: float | None
    descent_m: float | None
    min_elevation_m: float | None
    max_elevation_m: float | None
    elapsed_time_s: float | None
    moving_time_s: float | None
    stopped_time_s: float | None
    recording_gap_s: float | None
    stop_count: int | None
    distance_while_stopped_m: float | None


@dataclass(frozen=True)
class Analysis:
    algorithm_version: str
    params: AnalysisParams
    params_hash: str
    summary: TrackSummary
    profile: tuple[ProfileBin, ...]
    climbs: tuple[ClimbSection, ...]
    gradient_distribution: dict[str, float]  # band label -> horizontal metres
    warnings: tuple[QualityWarning, ...]


def analyse(track: Track, params: AnalysisParams | None = None) -> Analysis:
    """Analyse ``track`` as given. With ``params.elevation = "dem:<id>"`` the
    caller must pass the track with that DEM series as its elevation (see
    ``reference_elevation.with_elevation``); a mismatch is refused so a result
    can never be labelled with the wrong elevation source."""
    p = params or AnalysisParams()
    uses_dem = dem_dataset_of(p.elevation) is not None
    if uses_dem != (track.elevation_source is ElevationSource.DEM_ENRICHED):
        raise ValueError(
            f"params.elevation is {p.elevation!r} but the track's elevation source "
            f"is {track.elevation_source!r}"
        )

    raw_distance = float(step_distances_m(track)[within_segment(track)].sum())
    increments = filtered_step_distances_m(track, p.distance_min_step_m)
    distance = float(increments.sum())

    distance_3d = None
    if track.has_elevation:
        src, dst = displacement_pairs(track, p.distance_min_step_m)
        rise = track.ele_raw_m[dst] - track.ele_raw_m[src]
        distance_3d = float(np.sqrt(increments[dst - 1] ** 2 + rise**2).sum())

    elevation = elevation_metrics(track, p.ascent_threshold_m)
    timing = time_metrics(
        track,
        increments,
        p.quality.gap_threshold_s,
        p.moving_window_s,
        p.moving_min_speed_m_s,
        p.stop_min_duration_s,
    )

    profile = build_profile(track, p.profile_bin_m, p.distance_min_step_m)
    climbs = find_climb_sections(
        profile,
        p.climb_min_gradient_percent,
        p.climb_max_interruption_m,
        p.climb_min_elevation_change_m,
    )

    summary = TrackSummary(
        distance_m=distance,
        distance_raw_m=raw_distance,
        distance_3d_m=distance_3d,
        ascent_m=elevation.ascent_m if elevation else None,
        descent_m=elevation.descent_m if elevation else None,
        min_elevation_m=elevation.min_elevation_m if elevation else None,
        max_elevation_m=elevation.max_elevation_m if elevation else None,
        elapsed_time_s=timing.elapsed_time_s if timing else None,
        moving_time_s=timing.moving_time_s if timing else None,
        stopped_time_s=timing.stopped_time_s if timing else None,
        recording_gap_s=timing.recording_gap_s if timing else None,
        stop_count=timing.stop_count if timing else None,
        distance_while_stopped_m=(timing.distance_while_stopped_m if timing else None),
    )

    warnings = check_track(track, p.quality) + _gradient_outliers(
        profile, p.gradient_outlier_percent
    )

    return Analysis(
        algorithm_version=ALGORITHM_VERSION,
        params=p,
        params_hash=params_hash(p),
        summary=summary,
        profile=profile,
        climbs=climbs,
        gradient_distribution=gradient_distribution(profile),
        warnings=warnings,
    )


def _gradient_outliers(
    profile: tuple[ProfileBin, ...], limit_percent: float
) -> tuple[QualityWarning, ...]:
    outliers = tuple(
        b.index
        for b in profile
        if b.gradient_percent is not None and abs(b.gradient_percent) > limit_percent
    )
    if not outliers:
        return ()
    return (
        QualityWarning(
            WarningCode.GRADIENT_OUTLIER,
            f"{len(outliers)} profile bin(s) steeper than {limit_percent:g} %; "
            "values are kept, check the elevation data.",
            count=len(outliers),
            bin_indices=outliers,
        ),
    )
