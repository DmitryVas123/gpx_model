"""Separate flat and vertical speed factors on DIN 33466 (Phase 7, step 2;
ADR-017).

Model: DIN 33466 with the horizontal speed scaled by ``kh`` and both vertical
rates scaled by ``kv``:

    h_i = distance_i / (H * kh),  v_i = (ascent_i / U + descent_i / D) / kv
    moving_i = max(h_i, v_i) + min(h_i, v_i) / 2

Ascent and descent are scaled together, keeping DIN's U : D ratio. On loops
ascent ≈ descent, so they cannot be told apart from moving time alone.

Fit: least squares of ``ln(predicted / observed)``, each route group weighted
once, by a deterministic two-stage grid search in ``(ln kh, ln kv)``. DIN's
max/min formula has kinks, which a grid handles without local-minimum
surprises. An optimum on the grid edge means the data does not determine the
parameters.

The two factors are separable only if routes differ in their mix of vertical
and horizontal time. The *ratio range* is max/min over route groups of the
reference ``v_i / h_i``, and it must reach ``split_min_ratio_range``.
"""

import math
from dataclasses import dataclass

import numpy as np

from alpha.domain.scenarios import AssumptionSource, DinAssumptions
from alpha.modelling.baseline import (
    ActivitySample,
    BaselineParams,
    FitStatus,
    Validation,
    t975,
)

# Search ranges and steps for ln k (factors from 0.2 to 8).
_LN_MIN, _LN_MAX = math.log(0.2), math.log(8.0)
_COARSE = 0.02
_FINE = 0.001
_FINE_HALF_WIDTH = 0.03


@dataclass(frozen=True)
class SplitSampleResult:
    sample: ActivitySample
    reference_horizontal_s: float  # DIN reference speeds, before the factors
    reference_vertical_s: float
    predicted_moving_s: float | None  # leave one route out
    error_ratio: float | None  # predicted / observed - 1


@dataclass(frozen=True)
class SplitFit:
    status: FitStatus
    samples: tuple[SplitSampleResult, ...]
    n_activities: int
    n_groups: int
    min_groups: int
    ratio_range: float | None  # max/min over groups of reference v / h
    min_ratio_range: float
    reference: DinAssumptions
    reason: str | None = None  # why nothing is fitted
    horizontal_factor: float | None = None
    vertical_factor: float | None = None
    horizontal_interval: tuple[float, float] | None = None  # 95 %, jackknife
    vertical_interval: tuple[float, float] | None = None
    residual_spread: float | None = None  # std of group mean ln(pred / obs)
    validation: Validation | None = None
    assumptions: DinAssumptions | None = None


def _din_moving(h: np.ndarray, v: np.ndarray) -> np.ndarray:
    result: np.ndarray = np.maximum(h, v) + np.minimum(h, v) / 2
    return result


def _objective(
    ln_kh: np.ndarray,
    ln_kv: np.ndarray,
    h: np.ndarray,
    v: np.ndarray,
    ln_obs: np.ndarray,
    weights: np.ndarray,
) -> np.ndarray:
    """Weighted squared log errors for every (kh, kv) pair on the grid."""
    kh = np.exp(ln_kh)[:, None, None]
    kv = np.exp(ln_kv)[None, :, None]
    predicted = _din_moving(h[None, None, :] / kh, v[None, None, :] / kv)
    residual = np.log(predicted) - ln_obs[None, None, :]
    result: np.ndarray = (weights[None, None, :] * residual**2).sum(axis=2)
    return result


def _fit_two(
    h: np.ndarray, v: np.ndarray, obs: np.ndarray, groups: list[int]
) -> tuple[float, float, bool]:
    """(ln kh, ln kv, on_edge). Each group has total weight 1."""
    counts: dict[int, int] = {}
    for g in groups:
        counts[g] = counts.get(g, 0) + 1
    weights = np.array([1.0 / counts[g] for g in groups])
    ln_obs = np.log(obs)

    grid = np.arange(_LN_MIN, _LN_MAX + _COARSE / 2, _COARSE)
    cost = _objective(grid, grid, h, v, ln_obs, weights)
    i, j = np.unravel_index(int(np.argmin(cost)), cost.shape)
    on_edge = i in (0, len(grid) - 1) or j in (0, len(grid) - 1)

    fine_h = np.arange(grid[i] - _FINE_HALF_WIDTH, grid[i] + _FINE_HALF_WIDTH, _FINE)
    fine_v = np.arange(grid[j] - _FINE_HALF_WIDTH, grid[j] + _FINE_HALF_WIDTH, _FINE)
    cost = _objective(fine_h, fine_v, h, v, ln_obs, weights)
    a, b = np.unravel_index(int(np.argmin(cost)), cost.shape)
    return float(fine_h[a]), float(fine_v[b]), on_edge


def fit_split_speeds(
    samples: list[ActivitySample], params: BaselineParams | None = None
) -> SplitFit:
    p = params or BaselineParams()
    ref = p.reference
    h = np.array([s.distance_m / ref.horizontal_speed_m_h * 3600 for s in samples])
    v = np.array(
        [
            (s.ascent_m / ref.ascent_rate_m_h + s.descent_m / ref.descent_rate_m_h)
            * 3600
            for s in samples
        ]
    )
    obs = np.array([s.moving_time_s for s in samples])
    groups = [s.group for s in samples]
    group_ids = sorted(set(groups))
    n_groups = len(group_ids)

    ratio_range = None
    if samples:
        by_group: dict[int, list[float]] = {}
        for g, hi, vi in zip(groups, h, v, strict=True):
            by_group.setdefault(g, []).append(math.log(max(vi, 1e-9) / max(hi, 1e-9)))
        ratios = [math.exp(float(np.mean(x))) for x in by_group.values()]
        ratio_range = max(ratios) / min(ratios)

    def results(predicted: list[float | None]) -> tuple[SplitSampleResult, ...]:
        return tuple(
            SplitSampleResult(
                sample=s,
                reference_horizontal_s=float(hi),
                reference_vertical_s=float(vi),
                predicted_moving_s=pred,
                error_ratio=None if pred is None else pred / s.moving_time_s - 1,
            )
            for s, hi, vi, pred in zip(samples, h, v, predicted, strict=True)
        )

    def not_fitted(status: FitStatus, reason: str) -> SplitFit:
        return SplitFit(
            status=status,
            samples=results([None] * len(samples)),
            n_activities=len(samples),
            n_groups=n_groups,
            min_groups=p.split_min_groups,
            ratio_range=ratio_range,
            min_ratio_range=p.split_min_ratio_range,
            reference=ref,
            reason=reason,
        )

    if n_groups < p.split_min_groups:
        return not_fitted(
            FitStatus.NOT_ENOUGH_DATA,
            f"{n_groups} of {p.split_min_groups} distinct routes",
        )
    assert ratio_range is not None
    if ratio_range < p.split_min_ratio_range:
        return not_fitted(
            FitStatus.NOT_IDENTIFIABLE,
            f"routes are too alike: vertical/horizontal mix varies by "
            f"x{ratio_range:.2f}, needs x{p.split_min_ratio_range:g}",
        )

    ln_kh, ln_kv, on_edge = _fit_two(h, v, obs, groups)
    if on_edge:
        return not_fitted(
            FitStatus.NOT_IDENTIFIABLE,
            "the best fit lies at the edge of the search range (factors 0.2 to 8)",
        )

    # Leave one route group out: refit, predict the held-out activities.
    loo: dict[int, tuple[float, float]] = {}
    for g in group_ids:
        keep = np.array([x != g for x in groups])
        kh_g, kv_g, _ = _fit_two(
            h[keep], v[keep], obs[keep], [x for x in groups if x != g]
        )
        loo[g] = (kh_g, kv_g)
    predicted: list[float | None] = []
    for s, hi, vi in zip(samples, h, v, strict=True):
        kh_g, kv_g = loo[s.group]
        predicted.append(
            float(
                _din_moving(
                    np.array(hi / math.exp(kh_g)), np.array(vi / math.exp(kv_g))
                )
            )
        )
    fitted = results(predicted)
    errors = np.array([abs(x.error_ratio) for x in fitted if x.error_ratio is not None])

    # Jackknife over route groups for the parameter intervals (log space).
    t = t975(n_groups - 1)

    def interval(estimate: float, index: int) -> tuple[float, float]:
        values = np.array([loo[g][index] for g in group_ids])
        se = math.sqrt(
            (n_groups - 1) / n_groups * float(((values - values.mean()) ** 2).sum())
        )
        return math.exp(estimate - t * se), math.exp(estimate + t * se)

    full = _din_moving(h / math.exp(ln_kh), v / math.exp(ln_kv))
    residual = np.log(full / obs)
    group_mean = [float(np.mean(residual[[x == g for x in groups]])) for g in group_ids]
    kh, kv = math.exp(ln_kh), math.exp(ln_kv)
    return SplitFit(
        status=FitStatus.FITTED,
        samples=fitted,
        n_activities=len(samples),
        n_groups=n_groups,
        min_groups=p.split_min_groups,
        ratio_range=ratio_range,
        min_ratio_range=p.split_min_ratio_range,
        reference=ref,
        horizontal_factor=kh,
        vertical_factor=kv,
        horizontal_interval=interval(ln_kh, 0),
        vertical_interval=interval(ln_kv, 1),
        residual_spread=float(np.std(group_mean, ddof=2)) if n_groups > 2 else None,
        validation=Validation(
            folds=n_groups,
            median_abs_error=float(np.median(errors)),
            max_abs_error=float(errors.max()),
        ),
        assumptions=DinAssumptions(
            horizontal_speed_m_h=ref.horizontal_speed_m_h * kh,
            ascent_rate_m_h=ref.ascent_rate_m_h * kv,
            descent_rate_m_h=ref.descent_rate_m_h * kv,
            source=AssumptionSource.FITTED,
            label=f"personal flat x{kh:.2f} / vertical x{kv:.2f} on "
            f"{ref.label or 'reference'} ({len(samples)} activities, "
            f"{n_groups} routes)",
        ),
    )
