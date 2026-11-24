"""Personal speed multiplier on DIN 33466 (Phase 7, step 1; ADR-016).

Model: ``observed moving time = DIN moving time(reference speeds) / k``. ``k``
is the user's speed relative to the reference (DIN 33466 standard by
default). Per activity, ``k_i`` is exact (``implied_speed_multiplier``); the
fit combines them:

-   in log space, because errors are multiplicative (20 % faster and 20 %
    slower are symmetric in ``ln k``);
-   per *route group* first, so a route walked three times counts once:
    activities are not independent within a route (ADR-016);
-   with intervals for the *average* ``k`` and, wider, for *one new
    activity*;
-   validated by leaving out one route group at a time.

Below ``min_groups`` nothing is fitted: the status says "not enough data".
"""

import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

import numpy as np

from alpha.domain.scenarios import (
    DIN_33466_STANDARD,
    AssumptionSource,
    DinAssumptions,
    ScenarioInputs,
    din33466_breakdown,
)
from alpha.domain.track import Effort

# Effort selections for fitting. Untagged activities count as normal.
EFFORT_SELECTIONS: dict[str, tuple[Effort | None, ...]] = {
    "normal": (Effort.NORMAL, None),
    "hard": (Effort.HARD,),
    "easy": (Effort.EASY,),
    "all": (Effort.EASY, Effort.NORMAL, Effort.HARD, None),
}

# Two-sided 95 % Student t quantiles by degrees of freedom (no SciPy needed).
_T975 = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365,
    8: 2.306, 9: 2.262, 10: 2.228, 12: 2.179, 15: 2.131, 20: 2.086, 25: 2.060,
    30: 2.042, 40: 2.021, 60: 2.000, 120: 1.980,
}  # fmt: skip


def t975(df: int) -> float:
    """Conservative: the quantile of the nearest tabulated df at or below."""
    if df < 1:
        raise ValueError("df must be >= 1")
    return _T975[max(k for k in _T975 if k <= df)] if df <= 120 else 1.960


@dataclass(frozen=True)
class ActivitySample:
    """One recorded activity, reduced to what the model uses (calculated
    from the stored track with one elevation series)."""

    track_id: int
    group: int  # route group: the linked plan's ID, else the track's own
    name: str | None
    started_at_utc: datetime | None
    distance_m: float
    ascent_m: float
    descent_m: float
    moving_time_s: float
    effort: Effort | None = None  # the user's annotation; None = not stated

    def __post_init__(self) -> None:
        if not self.moving_time_s > 0:
            raise ValueError("moving_time_s must be > 0")


@dataclass(frozen=True)
class BaselineParams:
    """Provisional gates and filters (ADR-016)."""

    min_groups: int = 5  # distinct routes before anything is fitted
    min_moving_s: float = 1800.0  # shorter activities are too noisy
    activity_types: tuple[str, ...] = ("hiking", "walking")
    reference: DinAssumptions = DIN_33466_STANDARD  # speeds that k scales
    # Flat/vertical split (ADR-017): two parameters need more routes, and
    # routes that differ in their vertical/horizontal mix.
    split_min_groups: int = 8
    split_min_ratio_range: float = 1.5
    # Which annotated efforts the models are fitted on (ADR-018): a planning
    # baseline describes ordinary hikes, so hard and easy ones stay out.
    effort: str = "normal"

    @property
    def efforts(self) -> tuple[Effort | None, ...]:
        return EFFORT_SELECTIONS[self.effort]

    def __post_init__(self) -> None:
        if self.effort not in EFFORT_SELECTIONS:
            raise ValueError(f"effort must be one of {', '.join(EFFORT_SELECTIONS)}")
        if self.min_groups < 2 or self.split_min_groups < 3:
            raise ValueError(
                "min_groups must be >= 2 and split_min_groups >= 3 "
                "(validation needs it)"
            )
        if self.split_min_ratio_range < 1:
            raise ValueError("split_min_ratio_range must be >= 1")
        if self.min_moving_s < 0:
            raise ValueError("min_moving_s must be >= 0")


class FitStatus(StrEnum):
    FITTED = "fitted"
    NOT_ENOUGH_DATA = "not enough data"
    NOT_IDENTIFIABLE = "not identifiable"  # the data cannot separate the parameters


@dataclass(frozen=True)
class SampleResult:
    sample: ActivitySample
    reference_moving_s: float  # DIN moving time at the reference speeds
    implied_multiplier: float  # observed: reference_moving_s / moving_time_s
    # Leave-one-route-out: predicted with k fitted on the other groups.
    predicted_moving_s: float | None
    error_ratio: float | None  # predicted / observed - 1


@dataclass(frozen=True)
class Validation:
    folds: int  # route groups left out in turn
    median_abs_error: float  # of predicted / observed - 1, over activities
    max_abs_error: float


@dataclass(frozen=True)
class MultiplierFit:
    status: FitStatus
    samples: tuple[SampleResult, ...]
    n_activities: int
    n_groups: int
    min_groups: int
    reference: DinAssumptions
    multiplier: float | None = None  # geometric mean over route groups
    spread: float | None = None  # sample std of ln k between groups
    average_interval: tuple[float, float] | None = None  # 95 %, the mean k
    single_interval: tuple[float, float] | None = None  # 95 %, one new activity
    validation: Validation | None = None
    assumptions: DinAssumptions | None = None  # reference speeds x k, fitted


def _group_means(log_k: np.ndarray, groups: list[int]) -> dict[int, float]:
    by_group: dict[int, list[float]] = {}
    for value, g in zip(log_k.tolist(), groups, strict=True):
        by_group.setdefault(g, []).append(value)
    return {g: float(np.mean(v)) for g, v in by_group.items()}


def fit_speed_multiplier(
    samples: list[ActivitySample], params: BaselineParams | None = None
) -> MultiplierFit:
    p = params or BaselineParams()
    reference_moving = [
        din33466_breakdown(
            ScenarioInputs(s.distance_m, s.ascent_m, s.descent_m), p.reference
        ).moving_s
        for s in samples
    ]
    implied = np.array(
        [r / s.moving_time_s for r, s in zip(reference_moving, samples, strict=True)]
    )
    groups = [s.group for s in samples]
    means = _group_means(np.log(implied), groups) if samples else {}
    n_groups = len(means)

    def results(predicted: list[float | None]) -> tuple[SampleResult, ...]:
        return tuple(
            SampleResult(
                sample=s,
                reference_moving_s=r,
                implied_multiplier=float(k),
                predicted_moving_s=pred,
                error_ratio=None if pred is None else pred / s.moving_time_s - 1,
            )
            for s, r, k, pred in zip(
                samples, reference_moving, implied, predicted, strict=True
            )
        )

    if n_groups < p.min_groups:
        return MultiplierFit(
            status=FitStatus.NOT_ENOUGH_DATA,
            samples=results([None] * len(samples)),
            n_activities=len(samples),
            n_groups=n_groups,
            min_groups=p.min_groups,
            reference=p.reference,
        )

    values = np.array(list(means.values()))
    mean_log = float(values.mean())
    sd = float(values.std(ddof=1))
    t = t975(n_groups - 1)
    half_mean = t * sd / math.sqrt(n_groups)
    half_single = t * sd * math.sqrt(1 + 1 / n_groups)
    k = math.exp(mean_log)

    # Leave one route group out: fit on the others, predict the held-out ones.
    predicted: list[float | None] = []
    for s, r in zip(samples, reference_moving, strict=True):
        others = [m for g, m in means.items() if g != s.group]
        predicted.append(r / math.exp(float(np.mean(others))))
    fitted = results(predicted)
    errors = np.array([abs(x.error_ratio) for x in fitted if x.error_ratio is not None])

    ref = p.reference
    return MultiplierFit(
        status=FitStatus.FITTED,
        samples=fitted,
        n_activities=len(samples),
        n_groups=n_groups,
        min_groups=p.min_groups,
        reference=ref,
        multiplier=k,
        spread=sd,
        average_interval=(
            math.exp(mean_log - half_mean),
            math.exp(mean_log + half_mean),
        ),
        single_interval=(
            math.exp(mean_log - half_single),
            math.exp(mean_log + half_single),
        ),
        validation=Validation(
            folds=n_groups,
            median_abs_error=float(np.median(errors)),
            max_abs_error=float(errors.max()),
        ),
        assumptions=DinAssumptions(
            horizontal_speed_m_h=ref.horizontal_speed_m_h * k,
            ascent_rate_m_h=ref.ascent_rate_m_h * k,
            descent_rate_m_h=ref.descent_rate_m_h * k,
            source=AssumptionSource.FITTED,
            label=f"personal multiplier x{k:.2f} on {ref.label or 'reference'} "
            f"({len(samples)} activities, {n_groups} routes)",
        ),
    )


# --- effort (ADR-018) -------------------------------------------------------------


@dataclass(frozen=True)
class EffortGroup:
    """Observed: geometric mean of the implied multipliers per effort label.
    Descriptive only: efforts may differ in terrain as well."""

    effort: Effort | None  # None = not stated
    n_activities: int
    mean_implied_multiplier: float


def effort_groups(
    samples: list[ActivitySample], reference: DinAssumptions
) -> tuple[EffortGroup, ...]:
    order = [Effort.EASY, Effort.NORMAL, Effort.HARD, None]
    result = []
    for effort in order:
        chosen = [s for s in samples if s.effort == effort]
        if not chosen:
            continue
        logs = [
            math.log(_reference_moving(s, reference) / s.moving_time_s) for s in chosen
        ]
        result.append(EffortGroup(effort, len(chosen), math.exp(sum(logs) / len(logs))))
    return tuple(result)


@dataclass(frozen=True)
class PaceVsModel:
    """An activity outside the fitted effort selection, against the fitted
    models: predicted / observed moving time (> 1: faster than the model)."""

    sample: ActivitySample
    vs_multiplier: float | None  # None when that model is not fitted
    vs_split: float | None


def pace_vs_models(
    samples: list[ActivitySample],
    multiplier: DinAssumptions | None,
    split: DinAssumptions | None,
) -> tuple[PaceVsModel, ...]:
    def ratio(s: ActivitySample, a: DinAssumptions | None) -> float | None:
        return None if a is None else _reference_moving(s, a) / s.moving_time_s

    return tuple(PaceVsModel(s, ratio(s, multiplier), ratio(s, split)) for s in samples)


def _reference_moving(s: ActivitySample, a: DinAssumptions) -> float:
    return din33466_breakdown(
        ScenarioInputs(s.distance_m, s.ascent_m, s.descent_m), a
    ).moving_s
