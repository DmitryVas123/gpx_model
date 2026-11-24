"""Flat/vertical speed factors (ADR-017): activities generated from known
factors with the DIN formula, so the fit must recover them."""

import math

import pytest

from alpha.domain.scenarios import AssumptionSource
from alpha.modelling import (
    ActivitySample,
    BaselineParams,
    FitStatus,
    fit_speed_multiplier,
    fit_split_speeds,
)

# Reference (DIN standard) times in hours for (distance km, ascent m = descent m).
ROUTES = [(20.0, 400.0), (12.0, 900.0), (8.0, 1400.0), (15.0, 600.0), (6.0, 1000.0),
          (25.0, 300.0), (10.0, 1200.0), (18.0, 800.0), (9.0, 500.0)]  # fmt: skip


def sample(
    track_id: int,
    km: float,
    climb: float,
    kh: float,
    kv: float,
    group: int | None = None,
) -> ActivitySample:
    h = km / 4.0 / kh  # hours
    v = (climb / 300.0 + climb / 500.0) / kv
    moving_h = max(h, v) + min(h, v) / 2
    return ActivitySample(
        track_id=track_id,
        group=track_id if group is None else group,
        name=None,
        started_at_utc=None,
        distance_m=km * 1000,
        ascent_m=climb,
        descent_m=climb,
        moving_time_s=moving_h * 3600,
    )


def activities(kh: float, kv: float, n: int = 9) -> list[ActivitySample]:
    return [sample(i, km, c, kh, kv) for i, (km, c) in enumerate(ROUTES[:n])]


def test_known_factors_are_recovered() -> None:
    fit = fit_split_speeds(activities(1.3, 2.5))
    assert fit.status is FitStatus.FITTED
    assert fit.horizontal_factor == pytest.approx(1.3, rel=0.002)  # 0.001 ln grid
    assert fit.vertical_factor == pytest.approx(2.5, rel=0.002)
    assert fit.validation is not None and fit.validation.max_abs_error < 0.005
    assert fit.horizontal_interval is not None
    lo, hi = fit.horizontal_interval
    # Noiseless data: as narrow as the 0.1 % grid step around the true value.
    assert lo <= 1.3 * 1.002 and hi >= 1.3 / 1.002 and hi / lo < 1.02
    a = fit.assumptions
    assert a is not None and a.source is AssumptionSource.FITTED
    assert a.horizontal_speed_m_h == pytest.approx(4000 * 1.3, rel=0.002)
    assert a.ascent_rate_m_h == pytest.approx(300 * 2.5, rel=0.002)
    assert a.descent_rate_m_h == pytest.approx(500 * 2.5, rel=0.002)


def test_split_beats_one_multiplier_when_flat_and_vertical_differ() -> None:
    data = activities(1.2, 3.0)
    params = BaselineParams()
    one = fit_speed_multiplier(data, params)
    two = fit_split_speeds(data, params)
    assert one.validation is not None and two.validation is not None
    assert two.validation.median_abs_error < 0.005 < one.validation.median_abs_error


def test_equal_factors_reduce_to_the_multiplier() -> None:
    fit = fit_split_speeds(activities(1.7, 1.7))
    assert fit.horizontal_factor == pytest.approx(1.7, rel=0.002)
    assert fit.vertical_factor == pytest.approx(1.7, rel=0.002)
    multiplier = fit_speed_multiplier(activities(1.7, 1.7)).multiplier
    assert multiplier == pytest.approx(1.7, rel=1e-9)


def test_routes_that_are_alike_cannot_separate_the_factors() -> None:
    same_mix = [sample(i, 10.0 + i, 500.0 * (1 + i / 10), 1.2, 2.0) for i in range(9)]
    fit = fit_split_speeds(same_mix)
    assert fit.ratio_range is not None and fit.ratio_range < 1.5
    assert fit.status is FitStatus.NOT_IDENTIFIABLE
    assert fit.reason is not None and "too alike" in fit.reason
    assert fit.horizontal_factor is None and fit.assumptions is None


def test_factors_outside_the_search_range_are_not_reported() -> None:
    fit = fit_split_speeds(activities(1.0, 20.0))  # vertical beyond x8
    assert fit.status is FitStatus.NOT_IDENTIFIABLE
    assert fit.reason is not None and "edge" in fit.reason


def test_not_enough_routes_and_repeats_count_once() -> None:
    few = fit_split_speeds(activities(1.2, 2.0, n=7))
    assert (
        few.status is FitStatus.NOT_ENOUGH_DATA
        and few.reason == "7 of 8 distinct routes"
    )
    repeats = activities(1.2, 2.0, n=7) + [
        sample(100 + i, 20.0, 400.0, 1.2, 2.0, group=0) for i in range(3)
    ]
    assert fit_split_speeds(repeats).n_groups == 7  # still 7 routes
    assert fit_split_speeds(repeats).status is FitStatus.NOT_ENOUGH_DATA


def test_more_routes_tighten_the_intervals_with_noise() -> None:
    noisy = [
        sample(
            i, km, c, 1.2 * (1.06 if i % 2 else 0.95), 2.4 * (0.94 if i % 3 else 1.05)
        )
        for i, (km, c) in enumerate(ROUTES)
    ]
    small = fit_split_speeds(noisy[:8])
    large = fit_split_speeds(noisy)
    assert small.vertical_interval is not None and large.vertical_interval is not None

    def width(i: tuple[float, float]) -> float:
        return math.log(i[1] / i[0])

    assert large.status is FitStatus.FITTED
    assert width(large.vertical_interval) < width(small.vertical_interval) * 1.5
    assert large.validation is not None and large.validation.median_abs_error < 0.1
