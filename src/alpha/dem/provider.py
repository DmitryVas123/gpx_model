"""Sample DEM elevation along a track, with a local tile cache (ADR-012).

Cache layout: ``<cache_dir>/<dataset>/<tile>.tif`` plus ``<tile>.json`` with
provenance (dataset, bounds, retrieval time). Cached tiles are used without
network access; downloads happen only when a tile is missing and an API key
was supplied.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import numpy as np
import rasterio

from alpha.dem import opentopography
from alpha.dem.datasets import DATASETS, DemDataset
from alpha.dem.opentopography import DemDownloadError
from alpha.dem.sampling import FloatArray, Grid, bilinear
from alpha.dem.tiles import Tile, assign_tiles

Fetcher = Callable[[str, tuple[float, float, float, float], str], bytes]


class DemUnavailableError(Exception):
    """Tiles are missing and cannot be downloaded (e.g. no API key)."""


@dataclass(frozen=True)
class TileUse:
    name: str
    downloaded: bool  # False: served from the local cache
    retrieved_at_utc: datetime


@dataclass(frozen=True)
class DemSample:
    dataset: DemDataset
    elevation_m: FloatArray  # one value per point; NaN where no data
    tiles: tuple[TileUse, ...]

    @property
    def retrieved_at_utc(self) -> datetime:
        """Oldest retrieval time among the tiles used."""
        return min(t.retrieved_at_utc for t in self.tiles)


class ElevationSampler(Protocol):
    """Samples one DEM dataset at WGS84 points (downloaded or local tiles)."""

    @property
    def dataset(self) -> DemDataset: ...

    def sample(self, lat_deg: FloatArray, lon_deg: FloatArray) -> DemSample: ...


class DemProvider:
    """OpenTopography datasets: fixed 0.25 degree tiles, downloaded on demand."""

    def __init__(
        self,
        cache_dir: Path,
        dataset_id: str,
        api_key: str | None,
        fetch: Fetcher | None = None,
    ) -> None:
        if dataset_id not in DATASETS:
            raise ValueError(
                f"unknown DEM dataset {dataset_id!r}; known: {', '.join(DATASETS)}"
            )
        self.dataset = DATASETS[dataset_id]
        self.directory = cache_dir / dataset_id
        self._api_key = api_key
        self._fetch = fetch

    def sample(self, lat_deg: FloatArray, lon_deg: FloatArray) -> DemSample:
        values = np.full(len(lat_deg), np.nan)
        if len(lat_deg) == 0:
            raise ValueError("no points to sample")
        tiles, owner = assign_tiles(lat_deg, lon_deg, self._cached)
        uses = []
        for k, tile in enumerate(tiles):
            uses.append(self._ensure(tile))
            mask = owner == k
            values[mask] = bilinear(self._grid(tile), lon_deg[mask], lat_deg[mask])
        return DemSample(self.dataset, values, tuple(uses))

    # --- cache --------------------------------------------------------------------

    def _cached(self, tile: Tile) -> bool:
        path = self._path(tile)
        return path.exists() and path.with_suffix(".json").exists()

    def _path(self, tile: Tile) -> Path:
        return self.directory / f"{tile.name}.tif"

    def _ensure(self, tile: Tile) -> TileUse:
        path = self._path(tile)
        meta = path.with_suffix(".json")
        if path.exists() and meta.exists():
            retrieved = datetime.fromisoformat(
                json.loads(meta.read_text(encoding="utf-8"))["retrieved_at_utc"]
            )
            return TileUse(tile.name, False, retrieved)
        if not self._api_key:
            raise DemUnavailableError(
                f"DEM tile {tile.name} is not cached and no API key is configured "
                "(set OPENTOPOGRAPHY_API_KEY)"
            )
        # Resolved at call time so tests can replace the network function.
        fetch = self._fetch or opentopography.fetch_geotiff
        data = fetch(self.dataset.id, tile.request_bounds(), self._api_key)
        retrieved = datetime.now(UTC).replace(microsecond=0)
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(data)
        temporary.replace(path)
        meta.write_text(
            json.dumps(
                {
                    "dataset": self.dataset.id,
                    "tile": tile.name,
                    "request_bounds": dict(
                        zip(
                            ("south", "north", "west", "east"),
                            tile.request_bounds(),
                            strict=True,
                        )
                    ),
                    "retrieved_at_utc": retrieved.isoformat(),
                    "source": "OpenTopography globaldem API",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return TileUse(tile.name, True, retrieved)

    def _grid(self, tile: Tile) -> Grid:
        with rasterio.open(self._path(tile)) as src:
            values = src.read(1).astype(np.float64)
            if src.nodata is not None:
                values[values == src.nodata] = np.nan
            t = src.transform
            if t.b != 0 or t.d != 0 or t.e >= 0:
                raise DemDownloadError(f"tile {tile.name} is not a north-up raster")
            return Grid(values=values, x0=t.c, y0=t.f, dx=t.a, dy=t.e)
