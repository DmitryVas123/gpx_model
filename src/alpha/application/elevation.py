"""DEM enrichment of stored tracks and comparison with recorded elevation (ADR-012).

Enrichment is opt-in: nothing here runs unless the user asks for it. A stored
DEM profile is reused without network access; tiles are downloaded only when a
profile is missing (or ``refresh`` is requested) and the provider has a key.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from pyproj import Transformer

from alpha.application.tracks import (
    DemProfileMissingError,
    Workspace,
    require_track,
)
from alpha.dem import (
    DATASETS,
    DemDataset,
    DemDownloadError,
    DemUnavailableError,
    ElevationSampler,
    MetalinkError,
    RemoteTile,
    TileDownload,
    download_tiles,
    read_metalink,
    tiles_for_points,
    verify_tile,
)
from alpha.dem.metalink import Downloader
from alpha.domain.analysis import AnalysisParams
from alpha.domain.reference_elevation import (
    ElevationComparison,
    IntArray,
    compare_elevation,
    interpolate_along_track,
    reference_knots,
)
from alpha.domain.terrain import (
    TerrainSlopeSummary,
    slope_probes,
    summarize_slope,
    terrain_slope_deg,
)
from alpha.domain.track import FloatArray, Track
from alpha.persistence import DemProfileRecord, TerrainSlopeRecord, TrackRecord

__all__ = [
    "DemProfileMissingError",
    "ElevationView",
    "EnrichBatch",
    "EnrichFailure",
    "TilePlan",
    "elevation_check",
    "enrich_all",
    "enrich_track",
    "get_tiles",
    "plan_tiles",
]

# Sample the raster about every 20 m along the track (≈ 2/3 of a 30 m pixel).
# Denser sampling turns GPS lateral jitter into vertical noise (ADR-012).
DEM_SAMPLE_SPACING_M = 20.0
# Terrain slope from central differences over +/- 15 m (a 30 m baseline: one
# COP30 pixel, and above the micro-relief of a 1 m model), ADR-015.
TERRAIN_SLOPE_BASELINE_M = 15.0


@dataclass(frozen=True)
class ElevationView:
    record: TrackRecord
    profile: DemProfileRecord
    comparison: ElevationComparison
    downloaded_tiles: tuple[str, ...]  # empty when nothing was fetched
    reused: bool  # True: stored profile used, nothing sampled
    slope: TerrainSlopeSummary | None = None
    slope_note: str | None = None  # why the slope is missing, if it is


def enrich_track(
    ws: Workspace,
    track_id: int,
    provider: ElevationSampler,
    *,
    refresh: bool = False,
    params: AnalysisParams | None = None,
) -> ElevationView:
    """Sample the provider's DEM along the track (elevation and terrain
    slope), store it, and compare. A stored profile is reused; a missing
    slope for it is computed from the same model (ADR-015)."""
    p = params or AnalysisParams()
    dataset = provider.dataset
    with ws.db.transaction() as repo:
        record = require_track(repo, track_id)
        track = repo.load_track(track_id)
        stored = None if refresh else repo.get_dem_profile(track_id, dataset.id)
        slope = None if refresh else repo.get_terrain_slope(track_id, dataset.id)

    knots = reference_knots(track, DEM_SAMPLE_SPACING_M)
    probes = slope_probes(
        track.lat_deg[knots], track.lon_deg[knots], TERRAIN_SLOPE_BASELINE_M
    )
    downloaded: tuple[str, ...] = ()
    reused = stored is not None
    note = None
    if stored is None:
        # One pass over the tiles: knots first, then the four slope probes.
        sample = provider.sample(
            np.concatenate((track.lat_deg[knots], probes.lat_deg.ravel())),
            np.concatenate((track.lon_deg[knots], probes.lon_deg.ravel())),
        )
        downloaded = tuple(t.name for t in sample.tiles if t.downloaded)
        k = len(knots)
        stored = DemProfileRecord(
            track_id=track_id,
            dataset_id=dataset.id,
            dataset_name=dataset.name,
            resolution_m=dataset.resolution_m,
            surface=dataset.surface,
            vertical_datum=dataset.vertical_datum,
            attribution=dataset.attribution,
            sample_spacing_m=DEM_SAMPLE_SPACING_M,
            tiles=tuple(t.name for t in sample.tiles),
            retrieved_at_utc=sample.retrieved_at_utc,
            created_at_utc=datetime.now(UTC).replace(microsecond=0),
            elevation_m=interpolate_along_track(track, knots, sample.elevation_m[:k]),
        )
        slope = _slope_record(
            track, knots, sample.elevation_m[k:].reshape(4, k), track_id, dataset.id
        )
        with ws.db.transaction() as repo:
            repo.save_dem_profile(stored)
            repo.save_terrain_slope(slope)
            # Cached analyses may have used the previous DEM series.
            repo.delete_analyses(track_id)
    elif slope is None:
        try:
            values = provider.sample(probes.lat_deg.ravel(), probes.lon_deg.ravel())
        except DemUnavailableError as exc:
            note = f"terrain slope not computed: {exc}"
        else:
            downloaded = tuple(t.name for t in values.tiles if t.downloaded)
            slope = _slope_record(
                track,
                knots,
                values.elevation_m.reshape(4, len(knots)),
                track_id,
                dataset.id,
            )
            with ws.db.transaction() as repo:
                repo.save_terrain_slope(slope)

    return _view(record, track, stored, slope, p, downloaded, reused, note)


def elevation_check(
    ws: Workspace, track_id: int, dataset_id: str, params: AnalysisParams | None = None
) -> ElevationView:
    """Compare with an already stored DEM profile; never touches the network."""
    p = params or AnalysisParams()
    with ws.db.transaction() as repo:
        record = require_track(repo, track_id)
        stored = repo.get_dem_profile(track_id, dataset_id)
        if stored is None:
            raise DemProfileMissingError(track_id, dataset_id)
        track = repo.load_track(track_id)
        slope = repo.get_terrain_slope(track_id, dataset_id)
    note = (
        None
        if slope is not None
        else f"terrain slope not stored yet; run 'alpha enrich {track_id} "
        f"--dataset {dataset_id}'"
    )
    return _view(record, track, stored, slope, p, (), True, note)


def _slope_record(
    track: Track,
    knots: IntArray,
    probe_values: FloatArray,
    track_id: int,
    dataset_id: str,
) -> TerrainSlopeRecord:
    at_knots = terrain_slope_deg(probe_values, TERRAIN_SLOPE_BASELINE_M)
    return TerrainSlopeRecord(
        track_id=track_id,
        dataset_id=dataset_id,
        baseline_m=TERRAIN_SLOPE_BASELINE_M,
        sample_spacing_m=DEM_SAMPLE_SPACING_M,
        created_at_utc=datetime.now(UTC).replace(microsecond=0),
        slope_deg=interpolate_along_track(track, knots, at_knots),
    )


def _view(
    record: TrackRecord,
    track: Track,
    profile: DemProfileRecord,
    slope: TerrainSlopeRecord | None,
    p: AnalysisParams,
    downloaded: tuple[str, ...],
    reused: bool,
    note: str | None,
) -> ElevationView:
    comparison = compare_elevation(
        track, profile.elevation_m, p.ascent_threshold_m, p.distance_min_step_m
    )
    summary = (
        None
        if slope is None
        else summarize_slope(
            track, slope.slope_deg, p.distance_min_step_m, slope.baseline_m
        )
    )
    return ElevationView(record, profile, comparison, downloaded, reused, summary, note)


@dataclass(frozen=True)
class EnrichFailure:
    track_id: int
    message: str


@dataclass(frozen=True)
class EnrichBatch:
    enriched: tuple[ElevationView, ...]
    failed: tuple[EnrichFailure, ...]
    skipped_track_ids: tuple[int, ...]  # already had a profile (no ``refresh``)
    stopped: bool  # a download failed; the remaining tracks were not attempted


def enrich_all(
    ws: Workspace,
    provider: ElevationSampler,
    *,
    refresh: bool = False,
    params: AnalysisParams | None = None,
) -> EnrichBatch:
    """Enrich every stored track that has no profile for the provider's dataset
    (every track with ``refresh``).

    A track whose tiles are missing while downloads are unavailable is reported
    and the next one is tried (its tiles may be cached). A failed download stops
    the batch, so a network or key problem is not repeated for every track.
    """
    dataset_id = provider.dataset.id
    with ws.db.transaction() as repo:
        all_ids = [r.id for r in repo.list_tracks()]
        have = {t for t, ds in repo.dem_datasets_by_track().items() if dataset_id in ds}
    todo = all_ids if refresh else [t for t in all_ids if t not in have]
    skipped = tuple(t for t in all_ids if t not in todo)

    enriched: list[ElevationView] = []
    failed: list[EnrichFailure] = []
    for track_id in todo:
        try:
            enriched.append(
                enrich_track(ws, track_id, provider, refresh=refresh, params=params)
            )
        except DemUnavailableError as exc:
            failed.append(EnrichFailure(track_id, str(exc)))
        except DemDownloadError as exc:
            failed.append(EnrichFailure(track_id, str(exc)))
            return EnrichBatch(tuple(enriched), tuple(failed), skipped, True)
    return EnrichBatch(tuple(enriched), tuple(failed), skipped, False)


# --- local tiles from a metalink (ADR-013) -----------------------------------------


@dataclass(frozen=True)
class TilePlan:
    dataset: DemDataset
    directory: Path
    listed: int  # tiles in the metalink
    selected: tuple[RemoteTile, ...]
    for_tracks: bool
    outside_metalink: int  # 1 km squares under tracks the metalink lacks
    already_present: int

    @property
    def to_download(self) -> int:
        return len(self.selected) - self.already_present

    @property
    def hosts(self) -> tuple[str, ...]:
        return self.dataset.download_hosts


def plan_tiles(
    ws: Workspace,
    dem_dir: Path,
    metalink: bytes,
    dataset_id: str,
    *,
    for_tracks: bool,
) -> TilePlan:
    """Which tiles of a metalink to download: all of them, or only those
    under stored tracks (with seam neighbours)."""
    dataset = DATASETS.get(dataset_id)
    if dataset is None or dataset.source != "local" or not dataset.download_hosts:
        raise MetalinkError(f"{dataset_id!r} cannot be downloaded from a metalink")
    tiles = read_metalink(metalink, dataset)
    listed = len(tiles)
    outside = 0
    if for_tracks:
        with ws.db.transaction() as repo:
            loaded = [repo.load_track(r.id) for r in repo.list_tracks()]
        if not loaded:
            raise MetalinkError("no stored tracks; import tracks first")
        lat = np.concatenate([t.lat_deg for t in loaded])
        lon = np.concatenate([t.lon_deg for t in loaded])
        to_crs = Transformer.from_crs(
            "EPSG:4326", dataset.horizontal_crs, always_xy=True
        )
        xs, ys = to_crs.transform(lon, lat)
        tiles, outside = tiles_for_points(
            tiles, np.asarray(xs, dtype=np.float64), np.asarray(ys, dtype=np.float64)
        )
    directory = dem_dir / dataset.id
    present = sum(1 for t in tiles if (directory / t.name).is_file())
    return TilePlan(
        dataset,
        directory,
        listed,
        tuple(tiles),
        for_tracks,
        outside,
        present,
    )


def get_tiles(
    plan: TilePlan,
    fetch: Downloader | None = None,
    progress: Callable[[int, int, str], None] | None = None,
) -> TileDownload:
    def verify(path: Path, tile: RemoteTile) -> None:
        verify_tile(path, tile, plan.dataset)

    return download_tiles(
        list(plan.selected), plan.directory, verify, fetch=fetch, progress=progress
    )
