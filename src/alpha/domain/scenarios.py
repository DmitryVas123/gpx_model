"""Duration scenarios from explicit assumptions (DATA_MODEL.md §7.2, ADR-005).

A scenario is an **estimate**, never an observation. Every result carries the
method, the assumptions (and where they came from), and a breakdown.

Structure: a *method* (formula, registered by name) is applied to an analysis
with *assumptions* supplied by the caller. Assumptions may be typed in, taken
from a labelled preset, or, later, fitted from personal history by code outside
the domain; the domain only records their source.
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from alpha.domain.analysis import Analysis, TrackSummary

DEFAULT_SPEED_MULTIPLIERS: tuple[float, ...] = (1.2, 1.0, 0.8)


class AssumptionSource(StrEnum):
    USER = "user"  # typed in by the user
    PRESET = "preset"  # a named, published preset
    FITTED = "fitted"  # derived from personal history (post-MVP, outside domain)


@dataclass(frozen=True)
class DinAssumptions:
    """Inputs of DIN 33466. Speeds in metres per hour, times in seconds."""

    horizontal_speed_m_h: float
    ascent_rate_m_h: float
    descent_rate_m_h: float
    break_s: float = 0.0
    delay_s: float = 0.0
    source: AssumptionSource = AssumptionSource.USER
    label: str | None = None  # e.g. preset name

    def __post_init__(self) -> None:
        for name in ("horizontal_speed_m_h", "ascent_rate_m_h", "descent_rate_m_h"):
            if not getattr(self, name) > 0:
                raise ValueError(f"{name} must be > 0")
        for name in ("break_s", "delay_s"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be >= 0")


# Published reference values of DIN 33466 (also used by the Alpine clubs). They
# describe an average hiker on marked trails, not this user.
DIN_33466_STANDARD = DinAssumptions(
    horizontal_speed_m_h=4000.0,
    ascent_rate_m_h=300.0,
    descent_rate_m_h=500.0,
    source=AssumptionSource.PRESET,
    label="DIN 33466 standard",
)


@dataclass(frozen=True)
class ScenarioBreakdown:
    horizontal_s: float  # distance / horizontal speed
    vertical_s: float  # ascent / ascent rate + descent / descent rate
    moving_s: float  # max(h, v) + min(h, v) / 2
    break_s: float
    delay_s: float

    @property
    def total_s(self) -> float:
        return self.moving_s + self.break_s + self.delay_s


@dataclass(frozen=True)
class DurationScenario:
    label: str  # "faster" | "baseline" | "slower" | "x<multiplier>"
    speed_multiplier: float  # applied to all three speeds
    estimated_duration_s: float  # moving + breaks + delay
    breakdown: ScenarioBreakdown


@dataclass(frozen=True)
class ScenarioInputs:
    """The analysed values the method used (all calculated, never estimated)."""

    distance_m: float
    ascent_m: float
    descent_m: float


@dataclass(frozen=True)
class ScenarioResult:
    method: str
    method_version: str
    assumptions: DinAssumptions
    algorithm_version: str  # of the analysis the inputs came from
    params_hash: str
    inputs: ScenarioInputs
    scenarios: tuple[DurationScenario, ...]  # ordered as the multipliers given
    uncertainty_notes: tuple[str, ...]


class ScenarioInputError(ValueError):
    """The analysis lacks a value the method needs (e.g. unknown ascent)."""


# --- DIN 33466 ----------------------------------------------------------------------


def din33466_breakdown(
    inputs: ScenarioInputs, a: DinAssumptions, speed_multiplier: float = 1.0
) -> ScenarioBreakdown:
    """Walking time by DIN 33466: horizontal and vertical times are computed
    separately; the larger counts in full, the smaller by half."""
    if not speed_multiplier > 0:
        raise ValueError("speed_multiplier must be > 0")
    horizontal = inputs.distance_m / (a.horizontal_speed_m_h * speed_multiplier) * 3600
    vertical = (
        inputs.ascent_m / (a.ascent_rate_m_h * speed_multiplier)
        + inputs.descent_m / (a.descent_rate_m_h * speed_multiplier)
    ) * 3600
    moving = max(horizontal, vertical) + min(horizontal, vertical) / 2
    return ScenarioBreakdown(horizontal, vertical, moving, a.break_s, a.delay_s)


@dataclass(frozen=True)
class ScenarioMethod:
    name: str
    version: str
    description: str
    breakdown: Callable[[ScenarioInputs, DinAssumptions, float], ScenarioBreakdown]


# Registry of methods by name. A new method (e.g. pace per gradient band) is a new
# entry; where its parameters come from is not the method's concern.
SCENARIO_METHODS: dict[str, ScenarioMethod] = {
    "din33466": ScenarioMethod(
        name="din33466",
        version="1",
        description="DIN 33466 hiking time: max(h, v) + min(h, v) / 2",
        breakdown=din33466_breakdown,
    ),
}


def scenario_inputs(summary: TrackSummary) -> ScenarioInputs:
    if summary.ascent_m is None or summary.descent_m is None:
        raise ScenarioInputError(
            "ascent and descent are unknown (incomplete elevation); "
            "DIN 33466 needs both"
        )
    return ScenarioInputs(summary.distance_m, summary.ascent_m, summary.descent_m)


def _label(multiplier: float, multipliers: tuple[float, ...]) -> str:
    if multiplier == 1.0:
        return "baseline"
    if len(multipliers) == 3 and 1.0 in multipliers:
        return "faster" if multiplier > 1.0 else "slower"
    return f"x{multiplier:g}"


def run_scenarios(
    analysis: Analysis,
    assumptions: DinAssumptions,
    speed_multipliers: tuple[float, ...] = DEFAULT_SPEED_MULTIPLIERS,
    method: str = "din33466",
) -> ScenarioResult:
    """Apply a registered method to an analysis, once per speed multiplier.

    A multiplier scales all speeds (1.2 = 20 % faster); breaks and delay are
    added unchanged. The range shows sensitivity to pace only.
    """
    if not speed_multipliers:
        raise ValueError("at least one speed multiplier is needed")
    if method not in SCENARIO_METHODS:
        raise ValueError(f"unknown scenario method {method!r}")
    spec = SCENARIO_METHODS[method]
    inputs = scenario_inputs(analysis.summary)
    scenarios = tuple(
        DurationScenario(
            label=_label(k, speed_multipliers),
            speed_multiplier=k,
            estimated_duration_s=(b := spec.breakdown(inputs, assumptions, k)).total_s,
            breakdown=b,
        )
        for k in speed_multipliers
    )
    return ScenarioResult(
        method=spec.name,
        method_version=spec.version,
        assumptions=assumptions,
        algorithm_version=analysis.algorithm_version,
        params_hash=analysis.params_hash,
        inputs=inputs,
        scenarios=scenarios,
        uncertainty_notes=_notes(analysis, assumptions, speed_multipliers),
    )


def implied_speed_multiplier(
    inputs: ScenarioInputs, assumptions: DinAssumptions, observed_moving_s: float
) -> float | None:
    """The speed multiplier at which the method's moving time equals an observed
    moving time. DIN moving time scales exactly with 1 / multiplier, so this is
    a ratio, not a fit. One observation is a reference point, not a personal
    model. ``None`` if either time is zero."""
    baseline = din33466_breakdown(inputs, assumptions).moving_s
    if observed_moving_s <= 0 or baseline <= 0:
        return None
    return baseline / observed_moving_s


def _notes(
    analysis: Analysis, a: DinAssumptions, multipliers: tuple[float, ...]
) -> tuple[str, ...]:
    notes = [
        "Estimates follow from the stated assumptions only. Real time also "
        "depends on terrain, weather, snow, load, fitness, route-finding, and "
        "breaks.",
    ]
    if a.source is AssumptionSource.PRESET:
        notes.append(
            f"'{a.label}' are published reference values for an average hiker on "
            "marked trails, not personal values."
        )
    if len(multipliers) > 1:
        notes.append(
            f"The range ({min(multipliers):g}x to {max(multipliers):g}x speed) shows "
            "sensitivity to pace only, not to delays or conditions."
        )
    steep_m = sum(
        metres
        for band, metres in analysis.gradient_distribution.items()
        if band in ("< -25", "25..40", ">= 40")
    )
    if steep_m > 0:
        notes.append(
            f"{steep_m / 1000:.2f} km of the route is steeper than 25 %. The method "
            "uses only total distance, ascent, and descent, so steepness beyond that "
            "is not modelled."
        )
    if a.break_s == 0:
        notes.append("No breaks are included (break time 0).")
    return tuple(notes)


def din_assumptions(
    horizontal_speed_m_h: float | None = None,
    ascent_rate_m_h: float | None = None,
    descent_rate_m_h: float | None = None,
    break_s: float = 0.0,
    delay_s: float = 0.0,
) -> DinAssumptions:
    """DIN 33466 standard speeds unless any speed is given; then the assumptions
    are labelled as the user's own. Breaks and delay are always the user's."""
    preset = DIN_33466_STANDARD
    overridden = any(
        v is not None for v in (horizontal_speed_m_h, ascent_rate_m_h, descent_rate_m_h)
    )
    return DinAssumptions(
        horizontal_speed_m_h=(
            preset.horizontal_speed_m_h
            if horizontal_speed_m_h is None
            else horizontal_speed_m_h
        ),
        ascent_rate_m_h=preset.ascent_rate_m_h
        if ascent_rate_m_h is None
        else ascent_rate_m_h,
        descent_rate_m_h=(
            preset.descent_rate_m_h if descent_rate_m_h is None else descent_rate_m_h
        ),
        break_s=break_s,
        delay_s=delay_s,
        source=AssumptionSource.USER if overridden else preset.source,
        label=None if overridden else preset.label,
    )
