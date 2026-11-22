"""Sample elevation from GeoTIFF tiles the user stored locally (ADR-013).

Nothing is downloaded: the user puts tiles into ``<dem dir>/<dataset>/``
(subfolders allowed; any tile size; all tiles in one CRS). Tile footprints are
indexed in ``_index.json``; a file is opened again only when its size or
modification time changes. Track points are transformed from WGS84 into the
tiles' CRS with pyproj.
"""

import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import rasterio
import rasterio.errors
from pyproj import Transformer
from rasterio.merge import merge

from alpha.dem.datasets import DATASETS, DemDataset
from alpha.dem.metalink import MetalinkError, RemoteTile, km_tile_bounds
from alpha.dem.provider import DemSample, DemUnavailableError, TileUse
from alpha.dem.sampling import FloatArray, Grid, bilinear

INDEX_FILE = "_index.json"
INDEX_VERSION = 1


@dataclass(frozen=True)
class LocalTile:
    path: str  # relative to the dataset directory, POSIX separators
    size: int
    mtime_ns: int
    bounds: tuple[float, float, float, float]  # left, bottom, right, top
    res: tuple[float, float]  # pixel width, pixel height (both > 0)
    crs: str


class LocalTileProvider:
    def __init__(self, directory: Path, dataset_id: str) -> None:
        dataset = DATASETS.get(dataset_id)
        if dataset is None or dataset.source != "local":
            raise ValueError(f"{dataset_id!r} is not a local DEM dataset")
        self.dataset = dataset
        self.directory = directory

    def sample(self, lat_deg: FloatArray, lon_deg: FloatArray) -> DemSample:
        if len(lat_deg) == 0:
            raise ValueError("no points to sample")
        tiles = self.index()
        if not tiles:
            raise DemUnavailableError(
                f"no GeoTIFF tiles for {self.dataset.id} in {self.directory}; "
                f"put {self.dataset.obtain} there"
            )
        crs = sorted({t.crs for t in tiles})
        if len(crs) > 1:
            raise DemUnavailableError(f"{self.dataset.id} tiles mix CRS: {crs}")
        transformer = Transformer.from_crs("EPSG:4326", crs[0], always_xy=True)
        xs, ys = transformer.transform(lon_deg, lat_deg)
        x = np.asarray(xs, dtype=np.float64)
        y = np.asarray(ys, dtype=np.float64)

        # Each point belongs to the first tile whose footprint contains it.
        owner = np.full(len(x), -1)
        for i, t in enumerate(tiles):
            left, bottom, right, top = t.bounds
            hit = (owner < 0) & (x >= left) & (x < right) & (y > bottom) & (y <= top)
            owner[hit] = i
        if not (owner >= 0).any():
            raise DemUnavailableError(
                f"none of the {len(tiles)} {self.dataset.id} tiles in "
                f"{self.directory} covers this track"
            )

        values = np.full(len(x), np.nan)  # outside every tile: no data
        uses = []
        for i in np.unique(owner[owner >= 0]):
            tile = tiles[int(i)]
            mask = owner == i
            values[mask] = bilinear(self._padded_grid(tile, tiles), x[mask], y[mask])
            modified = datetime.fromtimestamp(tile.mtime_ns / 1e9, UTC)
            uses.append(
                TileUse(Path(tile.path).stem, False, modified.replace(microsecond=0))
            )
        return DemSample(self.dataset, values, tuple(uses))

    # --- tiles --------------------------------------------------------------------

    def index(self) -> list[LocalTile]:
        """Footprints of all tiles in the dataset directory."""
        if not self.directory.is_dir():
            return []
        index_path = self.directory / INDEX_FILE
        cached = _read_index(index_path)
        files = sorted(
            p
            for p in self.directory.rglob("*")
            if p.suffix.lower() in (".tif", ".tiff") and p.is_file()
        )
        tiles = []
        for path in files:
            rel = path.relative_to(self.directory).as_posix()
            stat = path.stat()
            known = cached.get(rel)
            if known and (known.size, known.mtime_ns) == (
                stat.st_size,
                stat.st_mtime_ns,
            ):
                tiles.append(known)
            else:
                tiles.append(_footprint(path, rel, stat.st_size, stat.st_mtime_ns))
        if tiles != list(cached.values()):
            index_path.write_text(
                json.dumps(
                    {"version": INDEX_VERSION, "tiles": [asdict(t) for t in tiles]}
                ),
                encoding="utf-8",
            )
        return tiles

    def _padded_grid(self, tile: LocalTile, tiles: list[LocalTile]) -> Grid:
        """The tile plus a one-pixel ring from its neighbours, so points next to
        a tile edge are interpolated across the seam instead of becoming NaN."""
        px, py = tile.res
        left, bottom, right, top = tile.bounds
        bounds = (left - px, bottom - py, right + px, top + py)
        sources = [
            self.directory / t.path
            for t in tiles
            if t.bounds[0] < bounds[2]
            and t.bounds[2] > bounds[0]
            and t.bounds[1] < bounds[3]
            and t.bounds[3] > bounds[1]
        ]
        array, transform = merge(
            sources, bounds=bounds, res=(px, py), nodata=math.nan, dtype="float64"
        )
        return Grid(
            values=np.asarray(array[0], dtype=np.float64),
            x0=transform.c,
            y0=transform.f,
            dx=transform.a,
            dy=transform.e,
        )


def _read_index(path: Path) -> dict[str, LocalTile]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("version") != INDEX_VERSION:
            return {}
        tiles = [
            LocalTile(
                path=item["path"],
                size=int(item["size"]),
                mtime_ns=int(item["mtime_ns"]),
                bounds=(
                    float(item["bounds"][0]),
                    float(item["bounds"][1]),
                    float(item["bounds"][2]),
                    float(item["bounds"][3]),
                ),
                res=(float(item["res"][0]), float(item["res"][1])),
                crs=str(item["crs"]),
            )
            for item in raw["tiles"]
        ]
    except (ValueError, KeyError, TypeError, IndexError):
        return {}  # unreadable index: rebuild it
    return {t.path: t for t in tiles}


def _footprint(path: Path, rel: str, size: int, mtime_ns: int) -> LocalTile:
    with rasterio.open(path) as src:
        t = src.transform
        if t.b != 0 or t.d != 0 or t.e >= 0:
            raise DemUnavailableError(f"tile {rel} is not a north-up raster")
        if src.crs is None:
            raise DemUnavailableError(f"tile {rel} has no coordinate reference system")
        b = src.bounds
        return LocalTile(
            path=rel,
            size=size,
            mtime_ns=mtime_ns,
            bounds=(b.left, b.bottom, b.right, b.top),
            res=(t.a, -t.e),
            crs=src.crs.to_string(),
        )


def verify_tile(path: Path, tile: RemoteTile, dataset: DemDataset) -> None:
    """A downloaded file must be a north-up GeoTIFF in the dataset's CRS whose
    footprint matches the 1 km square its name stands for."""
    try:
        footprint = _footprint(path, tile.name, 0, 0)
    except (rasterio.errors.RasterioIOError, DemUnavailableError) as exc:
        raise MetalinkError(f"{tile.name}: not a usable GeoTIFF ({exc})") from None
    if footprint.crs != dataset.horizontal_crs:
        raise MetalinkError(
            f"{tile.name}: CRS {footprint.crs}, expected {dataset.horizontal_crs}"
        )
    expected = km_tile_bounds(tile.name)
    if expected is not None and any(
        abs(a - b) > 1.0 for a, b in zip(footprint.bounds, expected, strict=True)
    ):
        raise MetalinkError(
            f"{tile.name}: covers {footprint.bounds}, but its name means {expected}; "
            "the tile naming assumption is wrong, nothing more is downloaded"
        )
