"""Elevation models (ADR-012, ADR-013): datasets, enrichment, the check."""

from typing import Any

from fastapi import APIRouter

from alpha.api import schemas
from alpha.api.context import ContextDep, WorkspaceDep, require_dataset
from alpha.api.errors import ERROR_RESPONSES
from alpha.api.reports import elevation_json, stored_elevation_json
from alpha.application.elevation import enrich_track
from alpha.dem import DATASETS, DEFAULT_DATASET

router = APIRouter(tags=["elevation"])


@router.get("/dem/datasets", response_model=schemas.DemDatasets)
def dem_datasets(context: ContextDep) -> dict[str, Any]:
    return {
        "datasets": [
            {
                "id": d.id,
                "name": d.name,
                "resolution_m": d.resolution_m,
                "surface": d.surface,
                "vertical_datum": d.vertical_datum,
                "attribution": d.attribution,
                "source": d.source,
                "obtain": d.obtain,
            }
            for d in DATASETS.values()
        ],
        "downloads_available": context.dem_downloads_available,
    }


@router.post(
    "/tracks/{track_id}/dem",
    response_model=schemas.ElevationCheck,
    responses=ERROR_RESPONSES,
    summary="Sample DEM elevation along the track, store it, and compare",
    description=(
        "Uses the stored profile if there is one (unless refresh=true). "
        "Otherwise missing tiles of a fixed 0.25 degree grid are downloaded "
        "from OpenTopography; the track itself is never sent. offline=true "
        "never downloads."
    ),
)
def enrich(
    track_id: int,
    context: ContextDep,
    ws: WorkspaceDep,
    dataset: str = DEFAULT_DATASET,
    refresh: bool = False,
    offline: bool = False,
) -> dict[str, Any]:
    sampler = context.dem_sampler(dataset, allow_download=not offline)
    with context.dem_lock:
        view = enrich_track(ws, track_id, sampler, refresh=refresh)
    return elevation_json(view)


@router.get(
    "/tracks/{track_id}/dem/{dataset}",
    response_model=schemas.ElevationCheck,
    responses=ERROR_RESPONSES,
    summary="Compare recorded elevation with the stored DEM profile (no network)",
)
def elevation(track_id: int, dataset: str, ws: WorkspaceDep) -> dict[str, Any]:
    return stored_elevation_json(ws, track_id, require_dataset(dataset))
