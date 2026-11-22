"""DEM adapter: datasets, tile cache, OpenTopography download, local tiles,
sampling.

The only package that imports rasterio or opens network connections.
"""

from pathlib import Path

from alpha.dem.datasets import DATASETS, DEFAULT_DATASET, DemDataset
from alpha.dem.local import LocalTileProvider, verify_tile
from alpha.dem.metalink import (
    MetalinkError,
    RemoteTile,
    TileDownload,
    download_tiles,
    read_metalink,
    tiles_for_points,
)
from alpha.dem.opentopography import DemDownloadError
from alpha.dem.provider import (
    DemProvider,
    DemSample,
    DemUnavailableError,
    ElevationSampler,
    Fetcher,
    TileUse,
)

__all__ = [
    "DATASETS",
    "DEFAULT_DATASET",
    "DemDataset",
    "DemDownloadError",
    "DemProvider",
    "DemSample",
    "DemUnavailableError",
    "ElevationSampler",
    "LocalTileProvider",
    "MetalinkError",
    "RemoteTile",
    "TileDownload",
    "TileUse",
    "download_tiles",
    "open_dem",
    "read_metalink",
    "tiles_for_points",
    "verify_tile",
]


def open_dem(
    dem_dir: Path,
    dataset_id: str,
    api_key: str | None,
    fetch: Fetcher | None = None,
) -> ElevationSampler:
    """The sampler for a dataset. Local datasets read ``<dem_dir>/<id>/`` and
    never use the network; ``api_key`` only matters for downloaded datasets."""
    dataset = DATASETS.get(dataset_id)
    if dataset is None:
        raise ValueError(
            f"unknown DEM dataset {dataset_id!r}; known: {', '.join(DATASETS)}"
        )
    if dataset.source == "local":
        return LocalTileProvider(dem_dir / dataset_id, dataset_id)
    return DemProvider(dem_dir, dataset_id, api_key, fetch)
