import numpy as np
import pytest

from alpha.domain.geodesy import (
    displacement_pairs,
    filtered_step_distances_m,
    geodesic_distances_m,
    step_distances_m,
    within_segment,
)
from tests.helpers import DEG_PER_METRE, LAT0, LON0, make_track

# Length of one degree of latitude along the WGS84 meridian starting at the
# equator (published value 110 574.4 m).
ONE_DEGREE_MERIDIAN_AT_EQUATOR_M = 110_574.389


def test_one_degree_of_latitude_at_equator() -> None:
    d = geodesic_distances_m(
        np.array([0.0]), np.array([0.0]), np.array([1.0]), np.array([0.0])
    )
    assert d[0] == pytest.approx(ONE_DEGREE_MERIDIAN_AT_EQUATOR_M, abs=0.01)


def test_identical_points_have_zero_distance() -> None:
    track = make_track([LAT0, LAT0], lon=[LON0, LON0])
    assert step_distances_m(track)[0] == 0.0


def test_step_distances_length_and_symmetry() -> None:
    lat = [LAT0, LAT0 + 0.001, LAT0]
    distances = step_distances_m(make_track(lat))
    assert len(distances) == 2
    assert distances[0] == pytest.approx(distances[1])
    assert distances[0] == pytest.approx(111.1, abs=0.5)


@pytest.mark.parametrize("n", [0, 1])
def test_fewer_than_two_points_have_no_steps(n: int) -> None:
    track = make_track([LAT0] * n)
    assert len(step_distances_m(track)) == 0
    assert len(within_segment(track)) == 0


def test_within_segment_marks_boundary_steps() -> None:
    track = make_track([LAT0] * 5, segment=[0, 0, 1, 1, 3])
    assert within_segment(track).tolist() == [True, False, True, False]


# --- jitter filter (ADR-010) -----------------------------------------------------


def test_zero_min_step_equals_raw_within_segment_distance() -> None:
    track = make_track(
        [LAT0, LAT0 + 0.0001, LAT0 + 0.0002, LAT0 + 0.01, LAT0 + 0.0101],
        segment=[0, 0, 0, 1, 1],
    )
    raw = step_distances_m(track)[within_segment(track)].sum()
    assert filtered_step_distances_m(track, 0.0).sum() == pytest.approx(raw)


def test_filter_removes_jitter_around_a_point() -> None:
    rng = np.random.default_rng(1)
    jitter = rng.uniform(-1.0, 1.0, 300) * DEG_PER_METRE  # +-1 m north/south
    track = make_track(
        np.concatenate(([LAT0], LAT0 + jitter, [LAT0 + 50 * DEG_PER_METRE]))
    )
    raw = step_distances_m(track).sum()
    filtered = filtered_step_distances_m(track, 3.0).sum()
    assert raw > 200.0
    assert filtered == pytest.approx(50.0, abs=3.0)


def test_filter_keeps_a_straight_line() -> None:
    lat = LAT0 + np.arange(0, 101, 1.0) * DEG_PER_METRE
    track = make_track(lat)
    raw = step_distances_m(track).sum()
    assert filtered_step_distances_m(track, 3.0).sum() == pytest.approx(raw, abs=3.0)


def test_filter_never_crosses_segment_boundaries() -> None:
    lat = LAT0 + np.array([0.0, 10.0, 1000.0, 1010.0]) * DEG_PER_METRE
    track = make_track(lat, segment=[0, 0, 1, 1])
    increments = filtered_step_distances_m(track, 3.0)
    assert increments[1] == 0.0
    assert increments.sum() == pytest.approx(20.0, rel=0.01)


def test_displacement_pairs_are_increasing_and_within_segments() -> None:
    lat = LAT0 + np.array([0.0, 1.0, 2.0, 5.0, 6.0, 20.0]) * DEG_PER_METRE
    track = make_track(lat, segment=[0, 0, 0, 0, 1, 1])
    src, dst = displacement_pairs(track, 3.0)
    assert src.tolist() == [0, 4]
    assert dst.tolist() == [3, 5]


def test_negative_min_step_rejected() -> None:
    with pytest.raises(ValueError):
        displacement_pairs(make_track([LAT0, LAT0]), -1.0)
