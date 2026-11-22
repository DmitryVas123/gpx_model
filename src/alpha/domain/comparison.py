"""Comparison of two analysed tracks (DATA_MODEL.md §7.1, Journey C).

Differences are reported, never judged: "material" is a display hint that a
difference is large, not a statement that a route is harder or unsafe.
"""

from dataclasses import dataclass

from alpha.domain.analysis import Analysis
from alpha.domain.climbs import ClimbSection, Direction
from alpha.domain.track import Track, TrackKind

# metric -> (kind of threshold, value). "ratio": material if |b/a - 1| >= value;
# "absolute": material if |b - a| >= value (in the metric's unit).
MATERIAL_RULES: dict[str, tuple[str, float]] = {
    "distance_m": ("ratio", 0.2),
    "distance_3d_m": ("ratio", 0.2),
    "ascent_m": ("ratio", 0.2),
    "descent_m": ("ratio", 0.2),
    "min_elevation_m": ("absolute", 200.0),
    "max_elevation_m": ("absolute", 200.0),
    "elapsed_time_s": ("ratio", 0.2),
    "moving_time_s": ("ratio", 0.2),
}
MATERIAL_BAND_M = 500.0


@dataclass(frozen=True)
class MetricDelta:
    metric: str
    a: float | None
    b: float | None
    delta: float | None  # b - a; None if either is unknown
    ratio: float | None  # b / a; None if either is unknown or a is 0
    material: bool | None  # None when it cannot be assessed


@dataclass(frozen=True)
class BandDelta:
    band: str
    a_m: float
    b_m: float
    delta_m: float
    material: bool


@dataclass(frozen=True)
class ComparisonResult:
    algorithm_version: str
    params_hash: str
    metrics: tuple[MetricDelta, ...]
    bands: tuple[BandDelta, ...]
    biggest_climb_a: ClimbSection | None  # largest net ascent
    biggest_climb_b: ClimbSection | None
    steepest_climb_a: ClimbSection | None  # highest mean gradient
    steepest_climb_b: ClimbSection | None
    notes: tuple[str, ...]


def compare(
    track_a: Track, analysis_a: Analysis, track_b: Track, analysis_b: Analysis
) -> ComparisonResult:
    """Compare B against A. Both analyses must use the same algorithm and params."""
    if (analysis_a.algorithm_version, analysis_a.params_hash) != (
        analysis_b.algorithm_version,
        analysis_b.params_hash,
    ):
        raise ValueError(
            "analyses are not comparable: algorithm version or params differ"
        )

    metrics = tuple(
        _metric_delta(
            name, getattr(analysis_a.summary, name), getattr(analysis_b.summary, name)
        )
        for name in MATERIAL_RULES
    )
    bands = tuple(
        BandDelta(
            band=band,
            a_m=a_m,
            b_m=analysis_b.gradient_distribution[band],
            delta_m=analysis_b.gradient_distribution[band] - a_m,
            material=abs(analysis_b.gradient_distribution[band] - a_m)
            >= MATERIAL_BAND_M,
        )
        for band, a_m in analysis_a.gradient_distribution.items()
    )
    return ComparisonResult(
        algorithm_version=analysis_a.algorithm_version,
        params_hash=analysis_a.params_hash,
        metrics=metrics,
        bands=bands,
        biggest_climb_a=_biggest(analysis_a),
        biggest_climb_b=_biggest(analysis_b),
        steepest_climb_a=_steepest(analysis_a),
        steepest_climb_b=_steepest(analysis_b),
        notes=_notes(track_a, analysis_a, track_b, analysis_b),
    )


def _metric_delta(name: str, a: float | None, b: float | None) -> MetricDelta:
    if a is None or b is None:
        return MetricDelta(name, a, b, None, None, None)
    delta = b - a
    ratio = b / a if a != 0 else None
    rule, threshold = MATERIAL_RULES[name]
    if rule == "absolute":
        material: bool | None = abs(delta) >= threshold
    else:
        material = None if ratio is None else abs(ratio - 1.0) >= threshold
    return MetricDelta(name, a, b, delta, ratio, material)


def _up_climbs(analysis: Analysis) -> list[ClimbSection]:
    return [c for c in analysis.climbs if c.direction is Direction.UP]


def _biggest(analysis: Analysis) -> ClimbSection | None:
    climbs = _up_climbs(analysis)
    return max(climbs, key=lambda c: c.elevation_change_m) if climbs else None


def _steepest(analysis: Analysis) -> ClimbSection | None:
    climbs = _up_climbs(analysis)
    return max(climbs, key=lambda c: c.mean_gradient_percent) if climbs else None


def _notes(
    track_a: Track, analysis_a: Analysis, track_b: Track, analysis_b: Analysis
) -> tuple[str, ...]:
    notes: list[str] = []
    for label, track, analysis in (
        ("A", track_a, analysis_a),
        ("B", track_b, analysis_b),
    ):
        if analysis.summary.moving_time_s is None:
            reason = (
                "planned route"
                if track.kind is TrackKind.PLANNED
                else "no complete timestamps"
            )
            notes.append(f"{label}: time metrics are unknown ({reason}).")
        if analysis.summary.ascent_m is None:
            notes.append(
                f"{label}: elevation metrics are unknown (incomplete elevation)."
            )
    if track_a.elevation_source != track_b.elevation_source:
        notes.append(
            f"Elevation sources differ ({track_a.elevation_source} vs "
            f"{track_b.elevation_source}); ascent and gradients may not be directly "
            "comparable."
        )
    if track_a.kind != track_b.kind:
        notes.append(
            f"Comparing {_article(track_a.kind)} {track_a.kind} track with "
            f"{_article(track_b.kind)} {track_b.kind} track; distances "
            "follow the recorded or planned line, which may differ from the path "
            "actually walked."
        )
    notes.append(
        "Differences describe the tracks only; they say nothing about conditions, "
        "technical difficulty, or safety."
    )
    return tuple(notes)


def _article(word: str) -> str:
    return "an" if word[:1] in "aeiou" else "a"
