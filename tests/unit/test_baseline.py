"""Personal speed multiplier (ADR-016): synthetic samples, hand-computed fits."""

import math

import pytest

from alpha.domain.scenarios import (
    DIN_33466_STANDARD,
    AssumptionSource,
    ScenarioInputs,
    din33466_breakdown,
)
from alpha.modelling import (
    ActivitySample,
    BaselineParams,
    FitStatus,
    fit_speed_multiplier,
)
from alpha.modelling.baseline import t975


def sample(track_id: int, k: float, group: int | None = None) -> ActivitySample:
    """An activity whose moving time is exactly DIN standard / k."""
    distance, ascent, descent = 10_000.0 + 500 * track_id, 600.0, 600.0
    reference = din33466_breakdown(
        ScenarioInputs(distance, ascent, descent), DIN_33466_STANDARD
    ).moving_s
    return ActivitySample(
        track_id=track_id,
        group=group if group is not None else track_id,
        name=f"a{track_id}",
        started_at_utc=None,
        distance_m=distance,
        ascent_m=ascent,
        descent_m=descent,
        moving_time_s=reference / k,
    )


def test_identical_activities_give_the_exact_multiplier() -> None:
    fit = fit_speed_multiplier([sample(i, 2.0) for i in range(5)])
    assert fit.status is FitStatus.FITTED
    assert fit.multiplier == pytest.approx(2.0)
    assert fit.spread == pytest.approx(0.0, abs=1e-12)
    assert fit.average_interval == pytest.approx((2.0, 2.0))
    assert fit.validation is not None
    assert fit.validation.max_abs_error == pytest.approx(0.0, abs=1e-12)
    a = fit.assumptions
    assert a is not None and a.source is AssumptionSource.FITTED
    assert a.horizontal_speed_m_h == pytest.approx(8000.0)
    assert a.ascent_rate_m_h == pytest.approx(600.0)
    assert a.descent_rate_m_h == pytest.approx(1000.0)
    assert "5 activities, 5 routes" in (a.label or "")


def test_intervals_and_validation_by_hand() -> None:
    logs = [0.0, 0.1, 0.2, 0.3, 0.4]
    fit = fit_speed_multiplier([sample(i, math.exp(v)) for i, v in enumerate(logs)])
    sd = math.sqrt(0.1 / 4)  # deviations -0.2 .. 0.2 around 0.2
    t = 2.776  # 95 %, df = 4
    assert fit.multiplier == pytest.approx(math.exp(0.2))
    assert fit.spread == pytest.approx(sd)
    half = t * sd / math.sqrt(5)
    assert fit.average_interval == pytest.approx(
        (math.exp(0.2 - half), math.exp(0.2 + half))
    )
    half1 = t * sd * math.sqrt(1.2)
    assert fit.single_interval == pytest.approx(
        (math.exp(0.2 - half1), math.exp(0.2 + half1))
    )
    # Leaving out ln k = 0: the other four average 0.25, so the prediction is
    # e^-0.25 times the observed moving time.
    assert fit.samples[0].error_ratio == pytest.approx(math.exp(-0.25) - 1)
    errors = sorted(abs(x.error_ratio or 0) for x in fit.samples)
    assert fit.validation is not None
    assert fit.validation.median_abs_error == pytest.approx(errors[2])
    assert fit.validation.max_abs_error == pytest.approx(math.exp(0.25) - 1)


def test_repeated_route_counts_once() -> None:
    repeats = [sample(i, 2.0, group=100) for i in range(3)]
    others = [sample(10 + i, 1.0) for i in range(4)]
    fit = fit_speed_multiplier(repeats + others)
    assert fit.n_activities == 7 and fit.n_groups == 5
    assert fit.multiplier == pytest.approx(2 ** (1 / 5))  # not 2 ** (3 / 7)
    # The three repeats are left out together and predicted from the others
    # (k = 1): twice their observed moving time.
    assert all(x.error_ratio == pytest.approx(1.0) for x in fit.samples[:3])


def test_not_enough_routes_fits_nothing() -> None:
    fit = fit_speed_multiplier([sample(i, 1.5) for i in range(4)])
    assert fit.status is FitStatus.NOT_ENOUGH_DATA
    assert (fit.n_groups, fit.min_groups) == (4, 5)
    assert fit.multiplier is None and fit.assumptions is None
    assert fit.validation is None
    assert [x.implied_multiplier for x in fit.samples] == pytest.approx([1.5] * 4)
    assert all(x.predicted_moving_s is None for x in fit.samples)
    empty = fit_speed_multiplier([])
    assert empty.status is FitStatus.NOT_ENOUGH_DATA and empty.n_groups == 0


def test_faster_activities_give_a_larger_multiplier() -> None:
    slow = fit_speed_multiplier([sample(i, 1.0 + 0.1 * i) for i in range(5)])
    fast = fit_speed_multiplier([sample(i, 2.0 * (1.0 + 0.1 * i)) for i in range(5)])
    assert slow.multiplier is not None and fast.multiplier is not None
    assert fast.multiplier == pytest.approx(2 * slow.multiplier)
    assert fast.spread == pytest.approx(slow.spread)  # spread is scale-free


def test_parameters_and_inputs_are_validated() -> None:
    with pytest.raises(ValueError):
        BaselineParams(min_groups=1)
    with pytest.raises(ValueError):
        BaselineParams(min_moving_s=-1)
    with pytest.raises(ValueError):
        sample(1, math.inf)  # moving time 0
    assert t975(4) == 2.776 and t975(11) == 2.228 and t975(500) == 1.96
    with pytest.raises(ValueError):
        t975(0)


def test_effort_groups_are_geometric_means_per_label() -> None:
    import dataclasses

    from alpha.domain.track import Effort
    from alpha.modelling import effort_groups

    hard = [
        dataclasses.replace(sample(i, k), effort=Effort.HARD)
        for i, k in ((1, 2.0), (2, 8.0))
    ]
    plain = [sample(3, 1.5)]
    groups = effort_groups(hard + plain, DIN_33466_STANDARD)
    assert [(g.effort, g.n_activities) for g in groups] == [(Effort.HARD, 2), (None, 1)]
    assert groups[0].mean_implied_multiplier == pytest.approx(4.0)  # sqrt(2 * 8)
    assert groups[1].mean_implied_multiplier == pytest.approx(1.5)


def test_pace_against_fitted_assumptions() -> None:
    from alpha.modelling import pace_vs_models

    fit = fit_speed_multiplier([sample(i, 1.5) for i in range(5)])
    (p,) = pace_vs_models([sample(9, 1.8)], fit.assumptions, None)
    assert p.vs_multiplier == pytest.approx(1.8 / 1.5)  # 20 % faster than fitted
    assert p.vs_split is None
