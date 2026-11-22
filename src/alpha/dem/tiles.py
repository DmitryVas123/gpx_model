"""Fixed tile grid for DEM requests (ADR-012).

Tiles are requested on a fixed 0.25° grid rather than around the track: the
provider never receives the outline of a route, and neighbouring tracks reuse
the same cached tiles. Each request adds a small margin so bilinear sampling
works up to the tile edge.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

TILE_DEG = 0.25
MARGIN_DEG = 0.002  # ~200 m; several 1" pixels beyond each edge
# A point this close to a neighbouring tile may be sampled from that tile's
# margin (half the margin keeps it well inside the raster).
COVER_DEG = MARGIN_DEG / 2


@dataclass(frozen=True, order=True)
class Tile:
    row: int  # floor(lat / TILE_DEG)
    col: int  # floor(lon / TILE_DEG)

    @property
    def south(self) -> float:
        return self.row * TILE_DEG

    @property
    def west(self) -> float:
        return self.col * TILE_DEG

    @property
    def name(self) -> str:
        lat, lon = self.south, self.west
        return (
            f"{'N' if lat >= 0 else 'S'}{abs(lat):06.2f}_"
            f"{'E' if lon >= 0 else 'W'}{abs(lon):07.2f}"
        )

    def request_bounds(self) -> tuple[float, float, float, float]:
        """(south, north, west, east) including the margin."""
        return (
            self.south - MARGIN_DEG,
            self.south + TILE_DEG + MARGIN_DEG,
            self.west - MARGIN_DEG,
            self.west + TILE_DEG + MARGIN_DEG,
        )


def tile_of(lat: float, lon: float) -> Tile:
    return Tile(math.floor(lat / TILE_DEG), math.floor(lon / TILE_DEG))


def tile_indices(
    lat_deg: npt.NDArray[np.float64], lon_deg: npt.NDArray[np.float64]
) -> tuple[npt.NDArray[np.int64], npt.NDArray[np.int64]]:
    rows = np.floor(lat_deg / TILE_DEG).astype(np.int64)
    cols = np.floor(lon_deg / TILE_DEG).astype(np.int64)
    return rows, cols


def tiles_for(
    lat_deg: npt.NDArray[np.float64], lon_deg: npt.NDArray[np.float64]
) -> list[Tile]:
    rows, cols = tile_indices(lat_deg, lon_deg)
    return sorted({Tile(int(r), int(c)) for r, c in zip(rows, cols, strict=True)})


def assign_tiles(
    lat_deg: npt.NDArray[np.float64],
    lon_deg: npt.NDArray[np.float64],
    available: Callable[[Tile], bool],
) -> tuple[list[Tile], npt.NDArray[np.int64]]:
    """Choose as few tiles as possible and assign each point to one.

    A point within ``COVER_DEG`` of a tile edge is also covered by the
    neighbouring tile's margin. Such a point uses a tile that is needed anyway
    or already ``available`` (cached) before a new one is added. So slope probes
    15 m beyond a tile edge, or a track along it, do not cause extra
    downloads. Returns the tiles and, per point, the index of its tile.
    """
    rows, cols = tile_indices(lat_deg, lon_deg)
    candidates: list[list[Tile]] = []
    for lat, lon, r, c in zip(lat_deg, lon_deg, rows, cols, strict=True):
        row, col = int(r), int(c)
        dy = float(lat) - row * TILE_DEG
        dx = float(lon) - col * TILE_DEG
        dr = [
            0,
            *([-1] if dy < COVER_DEG else []),
            *([1] if TILE_DEG - dy < COVER_DEG else []),
        ]
        dc = [
            0,
            *([-1] if dx < COVER_DEG else []),
            *([1] if TILE_DEG - dx < COVER_DEG else []),
        ]
        candidates.append([Tile(row + a, col + b) for a in dr for b in dc])

    # Points covered by one tile only decide the minimum set.
    chosen = {cands[0] for cands in candidates if len(cands) == 1}
    owner: list[Tile | None] = []
    for cands in candidates:
        pick = next((t for t in cands if t in chosen), None)
        if pick is None:
            pick = next((t for t in cands if available(t)), None)
            if pick is not None:
                chosen.add(pick)
        owner.append(pick)
    for i, cands in enumerate(candidates):
        if owner[i] is None:
            pick = next((t for t in cands if t in chosen), cands[0])
            chosen.add(pick)
            owner[i] = pick
    tiles = sorted(chosen)
    index = {t: k for k, t in enumerate(tiles)}
    return tiles, np.array([index[t] for t in owner if t is not None], dtype=np.int64)
