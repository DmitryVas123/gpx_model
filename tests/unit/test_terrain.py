"""Terrain slope (ADR-015): hand-computed slopes on synthetic planes."""

import math

import numpy as np
import pytest
from pyproj import Geod

from alpha.dem.tiles import COVER_DEG, TILE_DEG, Tile, assign_tiles
from alpha.domain.terrain import (
    slope_band_labels,
    slope_probes,
    summarize_slope,
    terrain_slope_deg,
)
from tests.helpers import LAT0, LON0, line_track


def test_slope_of_a_plane_from_probe_values() -> None:
    # z rises 1 m per m to the east and 0 to the north: 45 degrees.
    b = 15.0
    values = np.array([[100.0], [100.0], [115.0], [85.0]])  # N, S, E, W
    assert terrain_slope_deg(values, b)[0] == pytest.approx(45.0)
    # 3-4-5: dz/dx = 0.3, dz/dy = 0.4 -> |grad| = 0.5 -> atan(0.5)
    values = np.array([[106.0], [94.0], [104.5], [95.5]])
    assert terrain_slope_deg(values, b)[0] == pytest.approx(
        math.degrees(math.atan(0.5))
    )
    assert np.isnan(terrain_slope_deg(np.array([[1.0], [np.nan], [1.0], [1.0]]), b)[0])


def test_probes_are_baseline_metres_away_in_the_four_directions() -> None:
    probes = slope_probes(np.array([LAT0]), np.array([LON0]), 15.0)
    geod = Geod(ellps="WGS84")
    for row, azimuth in enumerate((0.0, 180.0, 90.0, 270.0)):
        az, _, dist = geod.inv(
            LON0, LAT0, probes.lon_deg[row, 0], probes.lat_deg[row, 0]
        )
        assert dist == pytest.approx(15.0, abs=1e-6)
        assert (az - azimuth + 180) % 360 - 180 == pytest.approx(0.0, abs=1e-6)
    with pytest.raises(ValueError):
        slope_probes(np.array([LAT0]), np.array([LON0]), 0.0)


def test_distribution_is_weighted_by_distance() -> None:
    track = line_track(10.0, 1000.0)  # 101 points, 100 steps of 10 m
    slope = np.where(np.arange(track.point_count) <= 70, 5.0, 35.0)
    slope[100] = np.nan  # last step unknown
    s = summarize_slope(track, slope, 3.0, 15.0)
    bands = {b.label: b.distance_m for b in s.bands}
    assert set(bands) == set(slope_band_labels())
    assert bands["<10"] == pytest.approx(700.0, abs=1)  # steps ending at points 1..70
    assert bands["30-40"] == pytest.approx(290.0, abs=1)
    assert s.unknown_m == pytest.approx(10.0, abs=0.1)
    assert s.median_deg == 5.0 and s.p90_deg == 35.0 and s.max_deg == 35.0
    assert s.max_at_m == pytest.approx(710.0, abs=1)


def test_unknown_everywhere() -> None:
    track = line_track(10.0, 100.0)
    s = summarize_slope(track, np.full(track.point_count, np.nan), 3.0, 15.0)
    assert s.median_deg is None and s.max_deg is None
    assert s.unknown_m == pytest.approx(100.0, abs=0.5)
    with pytest.raises(ValueError):
        summarize_slope(track, np.zeros(3), 3.0, 15.0)


# --- tile assignment (COP30 0.25 degree tiles) ----------------------------------------


def test_points_near_an_edge_use_a_neighbouring_tile_already_needed() -> None:
    corner_lat, corner_lon = 46.0, 8.0  # four tiles meet here
    inside = (corner_lat + 0.01, corner_lon + 0.01)  # only in tile (184, 32)
    probe = (corner_lat - COVER_DEG / 4, corner_lon - COVER_DEG / 4)  # SW of corner
    lat = np.array([inside[0], probe[0]])
    lon = np.array([inside[1], probe[1]])
    tiles, owner = assign_tiles(lat, lon, lambda t: False)
    assert tiles == [Tile(184, 32)] and owner.tolist() == [0, 0]


def test_cached_tiles_are_preferred_and_far_points_get_their_own() -> None:
    far = 46.0 - 3 * COVER_DEG  # well inside the southern tile
    near = 46.0 - COVER_DEG / 4  # southern tile, within the northern margin
    cached = Tile(183, 32)
    tiles, owner = assign_tiles(
        np.array([near]), np.array([8.1]), lambda t: t == cached
    )
    assert tiles == [cached] and owner.tolist() == [0]
    tiles, _ = assign_tiles(
        np.array([far, 46.1]), np.array([8.1, 8.1]), lambda t: False
    )
    assert tiles == [Tile(183, 32), Tile(184, 32)]
    assert TILE_DEG == 0.25
