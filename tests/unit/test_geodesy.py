import numpy as np
import pytest

from alpha.domain.geodesy import geodesic_distances_m, step_distances_m, within_segment
from tests.helpers import LAT0, LON0, make_track

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
