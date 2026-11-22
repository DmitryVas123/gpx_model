"""Pure parts of the DEM adapter: tile grid and bilinear sampling."""

import numpy as np
import pytest

from alpha.dem.sampling import Grid, bilinear
from alpha.dem.tiles import MARGIN_DEG, TILE_DEG, Tile, tile_of, tiles_for


def test_tile_of_and_names() -> None:
    assert tile_of(47.41, 10.28) == Tile(189, 41)
    assert tile_of(47.41, 10.28).name == "N047.25_E0010.25"
    assert tile_of(-0.1, -0.1).name == "S000.25_W0000.25"
    assert tile_of(47.25, 10.25) == Tile(189, 41)  # south-west edge is inclusive


def test_request_bounds_include_margin() -> None:
    south, north, west, east = Tile(189, 41).request_bounds()
    assert south == pytest.approx(47.25 - MARGIN_DEG)
    assert north == pytest.approx(47.25 + TILE_DEG + MARGIN_DEG)
    assert west == pytest.approx(10.25 - MARGIN_DEG)
    assert east == pytest.approx(10.5 + MARGIN_DEG)


def test_tiles_for_track_crossing_a_boundary() -> None:
    lat = np.array([47.24, 47.26, 47.27])
    lon = np.array([10.24, 10.24, 10.26])
    assert tiles_for(lat, lon) == [Tile(188, 40), Tile(189, 40), Tile(189, 41)]


def plane_grid() -> Grid:
    # 4 x 5 pixels of 0.1 degrees, north edge 1.0, west edge 0.0.
    lon_c = (np.arange(5) + 0.5) * 0.1
    lat_c = 1.0 - (np.arange(4) + 0.5) * 0.1
    lon, lat = np.meshgrid(lon_c, lat_c)
    return Grid(values=100 + 10 * lon + 20 * lat, x0=0.0, y0=1.0, dx=0.1, dy=-0.1)


def test_bilinear_is_exact_on_a_plane() -> None:
    lon = np.array([0.05, 0.12, 0.31, 0.449])
    lat = np.array([0.95, 0.77, 0.66, 0.651])
    values = bilinear(plane_grid(), lon, lat)
    np.testing.assert_allclose(values, 100 + 10 * lon + 20 * lat)


def test_bilinear_hits_pixel_centres() -> None:
    grid = plane_grid()
    assert bilinear(grid, np.array([0.15]), np.array([0.85]))[0] == pytest.approx(
        grid.values[1, 1]
    )


def test_outside_pixel_centres_is_nan() -> None:
    lon = np.array([0.01, 0.2, 0.6])
    lat = np.array([0.5, 0.99, 0.5])
    assert np.isnan(bilinear(plane_grid(), lon, lat)).all()


def test_no_data_neighbour_gives_nan_not_a_guess() -> None:
    grid = plane_grid()
    values = grid.values.copy()
    values[1, 1] = np.nan
    holed = Grid(values, grid.x0, grid.y0, grid.dx, grid.dy)
    # First point touches pixel [1, 1]; the second uses pixels [2..3, 3..4] only.
    result = bilinear(holed, np.array([0.12, 0.37]), np.array([0.82, 0.72]))
    assert np.isnan(result[0])
    assert not np.isnan(result[1])
