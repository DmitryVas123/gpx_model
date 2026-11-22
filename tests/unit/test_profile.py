import itertools

import numpy as np
import pytest

from alpha.domain.profile import (
    GRADIENT_BANDS,
    UNKNOWN_BAND,
    build_profile,
    cumulative_distance_m,
    gradient_band,
    gradient_distribution,
)
from tests.helpers import LAT0, line_track, make_track

STEP = 0.0001  # ~11.1 m


def test_cumulative_distance_ignores_segment_boundaries() -> None:
    track = make_track(
        [LAT0, LAT0 + STEP, LAT0 + 50 * STEP, LAT0 + 51 * STEP], segment=[0, 0, 1, 1]
    )
    c = cumulative_distance_m(track)
    assert c[1] == pytest.approx(c[3] - c[2])
    assert c[2] == c[1]


def test_bins_tile_the_distance_axis() -> None:
    track = line_track(10.0, 1020.0, lambda d: d * 0.0)
    bins = build_profile(track, 50.0)
    total = cumulative_distance_m(track)[-1]
    assert bins[0].start_distance_m == 0.0
    assert bins[-1].end_distance_m == pytest.approx(total)
    for a, b in itertools.pairwise(bins):
        assert a.end_distance_m == b.start_distance_m
    assert all(b.length_m == pytest.approx(50.0) for b in bins[:-1])


def test_constant_gradient_is_recovered() -> None:
    bins = build_profile(line_track(10.0, 1000.0, lambda d: 500 + 0.12 * d), 50.0)
    gradients = [b.gradient_percent for b in bins if b.gradient_percent is not None]
    assert gradients == pytest.approx([12.0] * len(gradients))
    assert bins[0].ele_start_m == pytest.approx(500.0)


def test_short_last_bin_has_no_gradient() -> None:
    track = line_track(1.0, 520.0, lambda d: 0.1 * d)
    bins = build_profile(track, 50.0)
    assert bins[-1].length_m < 25.0
    assert bins[-1].gradient_percent is None
    assert bins[-1].ele_change_m is not None


def test_segment_jump_is_not_a_gradient() -> None:
    track = make_track(
        [LAT0 + i * STEP for i in range(20)],
        ele=[1000.0] * 10 + [1100.0] * 10,
        segment=[0] * 10 + [1] * 10,
    )
    bins = build_profile(track, 50.0)
    assert all(b.ele_change_m == 0.0 for b in bins)
    assert all(b.gradient_percent in (0.0, None) for b in bins)


def test_without_elevation_everything_is_unknown() -> None:
    bins = build_profile(line_track(10.0, 300.0), 50.0)
    assert bins
    assert all(
        b.gradient_percent is None and b.ele_start_m is None and b.ele_change_m is None
        for b in bins
    )
    dist = gradient_distribution(bins)
    assert dist[UNKNOWN_BAND] == pytest.approx(sum(b.length_m for b in bins))


def test_elapsed_time_per_bin() -> None:
    track = line_track(10.0, 500.0, lambda d: d * 0.0, seconds_per_point=5.0)
    bins = build_profile(track, 50.0)
    assert sum(b.elapsed_s or 0.0 for b in bins) == pytest.approx(
        track.time_utc_s[-1] - track.time_utc_s[0]
    )
    assert bins[0].elapsed_s == pytest.approx(25.0, rel=0.01)


def test_zero_length_steps_do_not_break_interpolation() -> None:
    lat = [LAT0, LAT0 + STEP, LAT0 + STEP, LAT0 + STEP, LAT0 + 10 * STEP]
    track = make_track(
        lat, ele=[0.0, 10.0, 11.0, 12.0, 100.0], time=[0, 10, 70, 130, 200]
    )
    bins = build_profile(track, 50.0)
    assert all(np.isfinite(b.gradient_percent or 0.0) for b in bins)
    assert sum(b.elapsed_s or 0.0 for b in bins) == pytest.approx(200.0)


@pytest.mark.parametrize("n", [0, 1])
def test_too_few_points_give_no_bins(n: int) -> None:
    assert build_profile(make_track([LAT0] * n), 50.0) == ()


def test_stationary_track_gives_no_bins() -> None:
    assert build_profile(make_track([LAT0] * 5, ele=[1.0] * 5), 50.0) == ()


def test_invalid_bin_length() -> None:
    with pytest.raises(ValueError):
        build_profile(make_track([LAT0, LAT0 + STEP]), 0.0)


@pytest.mark.parametrize(
    ("gradient", "band"),
    [
        (None, UNKNOWN_BAND),
        (-30.0, "< -25"),
        (-25.0, "-25..-15"),
        (-5.0, "-5..5"),
        (0.0, "-5..5"),
        (5.0, "5..15"),
        (39.99, "25..40"),
        (40.0, ">= 40"),
        (400.0, ">= 40"),
    ],
)
def test_gradient_band_boundaries(gradient: float | None, band: str) -> None:
    assert gradient_band(gradient) == band


def test_distribution_has_every_band_and_sums_to_length() -> None:
    bins = build_profile(line_track(10.0, 2000.0, lambda d: 30 * np.sin(d / 200)), 50.0)
    dist = gradient_distribution(bins)
    assert list(dist) == [label for label, _, _ in GRADIENT_BANDS] + [UNKNOWN_BAND]
    assert sum(dist.values()) == pytest.approx(bins[-1].end_distance_m)


def test_results_do_not_depend_on_point_density() -> None:
    def hill(
        d: np.ndarray[tuple[int], np.dtype[np.float64]],
    ) -> np.ndarray[tuple[int], np.dtype[np.float64]]:
        return 1000 + 80 * np.sin(d / 400)

    dense = build_profile(line_track(1.0, 3000.0, hill), 50.0)
    sparse = build_profile(line_track(15.0, 3000.0, hill), 50.0)
    assert len(dense) == len(sparse)
    for a, b in zip(dense, sparse, strict=True):
        if a.gradient_percent is not None and b.gradient_percent is not None:
            assert a.gradient_percent == pytest.approx(b.gradient_percent, abs=0.3)
