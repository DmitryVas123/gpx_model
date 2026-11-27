"""Importing, listing, showing, editing, and deleting stored tracks."""

from typing import Any

from fastapi import APIRouter, Request, Response
from starlette.concurrency import run_in_threadpool

from alpha.api import schemas
from alpha.api.context import ParamsDep, WorkspaceDep
from alpha.api.errors import ERROR_RESPONSES, ApiError
from alpha.application.tracks import (
    delete_source_of_track,
    edit_track,
    import_file,
    list_tracks,
    show_track,
)
from alpha.gpx.reader import DEFAULT_MAX_BYTES
from alpha.report import list_item_report, record_report, stored_track_ref, track_report

router = APIRouter(tags=["tracks"])


@router.post(
    "/files",
    response_model=schemas.ImportResult,
    responses={**ERROR_RESPONSES, 201: {"model": schemas.ImportResult}},
    summary="Import a GPX file (raw body); idempotent by SHA-256",
)
async def upload(
    request: Request,
    response: Response,
    ws: WorkspaceDep,
    filename: str = "upload.gpx",
) -> dict[str, Any]:
    declared = request.headers.get("content-length")
    if (
        declared is not None
        and declared.isdigit()
        and int(declared) > DEFAULT_MAX_BYTES
    ):
        raise ApiError(413, "FILE_TOO_LARGE", f"limit is {DEFAULT_MAX_BYTES} bytes")
    data = await request.body()
    outcome = await run_in_threadpool(import_file, ws, filename, data)
    response.status_code = 201 if outcome.created else 200
    return {
        "created": outcome.created,
        "source_file": {
            "id": outcome.source.id,
            "sha256": outcome.source.sha256,
            "original_filename": outcome.source.original_filename,
            "size_bytes": outcome.source.size_bytes,
        },
        "tracks": [record_report(r) for r in outcome.tracks],
    }


@router.get("/tracks", response_model=list[schemas.TrackListItem])
def tracks(ws: WorkspaceDep) -> list[dict[str, Any]]:
    return [
        list_item_report(i.record, i.summary, i.dem_datasets) for i in list_tracks(ws)
    ]


@router.get(
    "/tracks/{track_id}",
    response_model=schemas.TrackAnalysis,
    responses=ERROR_RESPONSES,
    summary="Full analysis of a stored track",
)
def track(track_id: int, ws: WorkspaceDep, params: ParamsDep) -> dict[str, Any]:
    view = show_track(ws, track_id, params)
    r = view.record
    return {
        "stored": stored_track_ref(r.id, r.source_filename, r.planned_track_id),
        **track_report(view.track, view.analysis, view.dem),
    }


@router.patch(
    "/tracks/{track_id}",
    response_model=schemas.TrackRecord,
    responses=ERROR_RESPONSES,
    summary="Correct kind or elevation source, link to a plan, or set effort",
)
def edit(track_id: int, body: schemas.TrackEdit, ws: WorkspaceDep) -> dict[str, Any]:
    record = edit_track(
        ws,
        track_id,
        kind=body.kind,
        elevation_source=body.elevation_source,
        planned_track_id=body.planned_track_id,
        clear_planned_track=body.clear_planned_track,
        effort=body.effort,
        clear_effort=body.clear_effort,
    )
    return record_report(record)


@router.delete(
    "/tracks/{track_id}/source-file",
    response_model=schemas.Deletion,
    responses=ERROR_RESPONSES,
    summary="Delete the GPX file containing this track, with all its tracks",
)
def delete(track_id: int, ws: WorkspaceDep, dry_run: bool = False) -> dict[str, Any]:
    plan = delete_source_of_track(ws, track_id, confirm=not dry_run)
    return {
        "source_file_id": plan.source_file_id,
        "source_filename": plan.source_filename,
        "tracks": [record_report(r) for r in plan.tracks],
        "deleted": plan.deleted,
    }
