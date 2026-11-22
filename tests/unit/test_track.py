import math

import numpy as np
import pytest

from tests.helpers import LAT0, make_track


def test_columns_are_read_only_copies() -> None:
    lat = np.array([LAT0, LAT0 + 0.001])
    track = make_track(lat)
    lat[0] = 0.0
    assert track.lat_deg[0] == LAT0
    with pytest.raises(ValueError):
        track.lat_deg[0] = 1.0


def test_column_dtypes() -> None:
    track = make_track([LAT0], segment=[0])
    assert track.lat_deg.dtype == np.float64
    assert track.source_segment.dtype == np.int32


def test_length_mismatch_rejected() -> None:
    with pytest.raises(ValueError, match="lengths differ"):
        make_track([LAT0, LAT0], ele=[1.0])


@pytest.mark.parametrize("lat", [91.0, -90.5, math.nan])
def test_invalid_latitude_rejected(lat: float) -> None:
    with pytest.raises(ValueError):
        make_track([lat])


def test_decreasing_source_segment_rejected() -> None:
    with pytest.raises(ValueError, match="non-decreasing"):
        make_track([LAT0, LAT0, LAT0], segment=[0, 1, 0])


def test_empty_track_is_valid() -> None:
    track = make_track([])
    assert track.point_count == 0
    assert track.elevation_coverage == 0.0
    assert not track.has_elevation
    assert not track.has_timestamps


def test_coverage_counts_missing_values() -> None:
    track = make_track(
        [LAT0] * 4,
        ele=[1.0, math.nan, 2.0, 3.0],
        time=[0.0, 1.0, math.nan, math.nan],
        segment=[0, 0, 1, 1],
    )
    assert track.elevation_coverage == 0.75
    assert track.time_coverage == 0.5
    assert not track.has_elevation
    assert track.source_segment_count == 2
