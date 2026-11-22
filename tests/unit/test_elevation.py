import math

import numpy as np
import pytest

from alpha.domain.elevation import elevation_metrics, hysteresis_ascent_descent
from tests.helpers import LAT0, make_track


def series(*values: float) -> np.ndarray[tuple[int], np.dtype[np.float64]]:
    return np.array(values, dtype=np.float64)


def random_walk(
    seed: int, n: int = 2000
) -> np.ndarray[tuple[int], np.dtype[np.float64]]:
    rng = np.random.default_rng(seed)
    return 1000.0 + np.cumsum(rng.normal(0.0, 1.5, n))


def test_zero_threshold_is_plain_sum() -> None:
    assert hysteresis_ascent_descent(series(0, 2, 1, 4, 3), 0.0) == (5.0, 2.0)


@pytest.mark.parametrize("n", [0, 1])
def test_too_short_series(n: int) -> None:
    assert hysteresis_ascent_descent(series(*[5.0] * n), 3.0) == (0.0, 0.0)


def test_noise_below_threshold_is_ignored() -> None:
    assert hysteresis_ascent_descent(series(0, 1, 0, 1, 0, 2, 0), 3.0) == (0.0, 0.0)


def test_steady_climb_counted_in_full() -> None:
    assert hysteresis_ascent_descent(series(0, 1, 2, 3, 4, 5, 10, 30), 3.0) == (
        30.0,
        0.0,
    )


def test_peak_counted_in_full_despite_small_wiggle() -> None:
    up, down = hysteresis_ascent_descent(series(0, 50, 100, 98, 99, 50, 0), 3.0)
    assert (up, down) == (100.0, 100.0)


def test_swing_exactly_at_threshold_counts() -> None:
    assert hysteresis_ascent_descent(series(0, 3, 0), 3.0) == (3.0, 3.0)


def test_negative_threshold_rejected() -> None:
    with pytest.raises(ValueError):
        hysteresis_ascent_descent(series(0, 1), -1.0)


@pytest.mark.parametrize("seed", range(10))
@pytest.mark.parametrize("threshold", [0.0, 1.0, 3.0, 10.0])
def test_net_change_invariant(seed: int, threshold: float) -> None:
    ele = random_walk(seed)
    up, down = hysteresis_ascent_descent(ele, threshold)
    assert abs((up - down) - (ele[-1] - ele[0])) <= 2 * threshold + 1e-9


@pytest.mark.parametrize("seed", range(10))
def test_reversal_swaps_ascent_and_descent(seed: int) -> None:
    ele = random_walk(seed)
    up, down = hysteresis_ascent_descent(ele, 3.0)
    rev_up, rev_down = hysteresis_ascent_descent(ele[::-1], 3.0)
    # Start/end handling is not perfectly symmetric; each end can differ by
    # less than one threshold.
    assert rev_up == pytest.approx(down, abs=2 * 3.0)
    assert rev_down == pytest.approx(up, abs=2 * 3.0)


@pytest.mark.parametrize("seed", range(5))
def test_higher_threshold_never_increases_ascent(seed: int) -> None:
    ele = random_walk(seed)
    ascents = [hysteresis_ascent_descent(ele, t)[0] for t in (0, 1, 3, 5, 10)]
    assert ascents == sorted(ascents, reverse=True)


def test_metrics_require_complete_elevation() -> None:
    track = make_track([LAT0, LAT0, LAT0], ele=[1.0, math.nan, 3.0])
    assert elevation_metrics(track, 3.0) is None
    assert elevation_metrics(make_track([LAT0, LAT0]), 3.0) is None


def test_jump_between_source_segments_is_not_ascent() -> None:
    track = make_track(
        [LAT0] * 4, ele=[1000.0, 1010.0, 1100.0, 1110.0], segment=[0, 0, 1, 1]
    )
    metrics = elevation_metrics(track, 0.0)
    assert metrics is not None
    assert (metrics.ascent_m, metrics.descent_m) == (20.0, 0.0)
    assert (metrics.min_elevation_m, metrics.max_elevation_m) == (1000.0, 1110.0)
