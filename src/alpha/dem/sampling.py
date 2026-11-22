"""Bilinear sampling of a north-up raster (pure NumPy)."""

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]


@dataclass(frozen=True)
class Grid:
    """A north-up raster. ``values[row, col]`` covers the pixel whose top-left
    corner is ``(x0 + col * dx, y0 + row * dy)`` with ``dy < 0``. NaN = no data."""

    values: FloatArray
    x0: float  # west edge (in the raster's CRS units)
    y0: float  # north edge
    dx: float  # pixel width (> 0)
    dy: float  # pixel height (< 0)


def bilinear(grid: Grid, x: FloatArray, y: FloatArray) -> FloatArray:
    """Interpolate between the four surrounding pixel centres.

    Points outside the area covered by pixel centres, or next to a no-data
    pixel, get NaN: a missing value is never invented.
    """
    # Fractional position in pixel-centre coordinates.
    col = (x - grid.x0) / grid.dx - 0.5
    row = (y - grid.y0) / grid.dy - 0.5
    rows, cols = grid.values.shape
    c0 = np.floor(col).astype(np.int64)
    r0 = np.floor(row).astype(np.int64)
    inside = (c0 >= 0) & (r0 >= 0) & (c0 + 1 < cols) & (r0 + 1 < rows)

    result = np.full(len(x), np.nan)
    if not inside.any():
        return result
    c0i, r0i = c0[inside], r0[inside]
    fc = col[inside] - c0i
    fr = row[inside] - r0i
    v = grid.values
    top = v[r0i, c0i] * (1 - fc) + v[r0i, c0i + 1] * fc
    bottom = v[r0i + 1, c0i] * (1 - fc) + v[r0i + 1, c0i + 1] * fc
    result[inside] = top * (1 - fr) + bottom * fr  # NaN propagates from no-data
    return result
