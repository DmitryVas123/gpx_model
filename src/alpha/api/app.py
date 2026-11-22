"""HTTP API (ARCHITECTURE.md §8, ADR-011).

Route handlers only translate HTTP to use cases and results to JSON; all
calculations live in the domain, all SQL in persistence. JSON is built by
``alpha.report`` and validated against ``alpha.api.schemas``.
"""

import json
import logging
import threading
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from alpha import __version__
from alpha.api import schemas
from alpha.application.elevation import elevation_check, enrich_track
from alpha.application.scenarios import estimate_duration
from alpha.application.tracks import (
    DemProfileMissingError,
    InvalidEditError,
    TrackNotFoundError,
    Workspace,
    compare_tracks,
    delete_source_of_track,
    edit_track,
    import_file,
    list_tracks,
    show_track,
)
from alpha.dem import (
    DATASETS,
    DEFAULT_DATASET,
    DemDownloadError,
    DemUnavailableError,
    ElevationSampler,
    open_dem,
)
from alpha.domain.analysis import AnalysisParams
from alpha.domain.scenarios import (
    DEFAULT_SPEED_MULTIPLIERS,
    ScenarioInputError,
    din_assumptions,
)
from alpha.gpx.reader import DEFAULT_MAX_BYTES, GpxReadError, ReadErrorCode
from alpha.report import (
    comparison_report,
    elevation_check_report,
    list_item_report,
    record_report,
    scenario_report,
    stored_track_ref,
    track_report,
)

logger = logging.getLogger("alpha.api")
STATIC = Path(__file__).resolve().parent / "static"
_DEFAULTS = AnalysisParams()

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {"model": schemas.ErrorResponse, "description": "TRACK_NOT_FOUND"},
    409: {
        "model": schemas.ErrorResponse,
        "description": "INVALID_EDIT, or DEM_PROFILE_MISSING (run enrichment first)",
    },
    413: {"model": schemas.ErrorResponse, "description": "FILE_TOO_LARGE"},
    502: {
        "model": schemas.ErrorResponse,
        "description": "DEM_DOWNLOAD_FAILED (OpenTopography refused or unreachable)",
    },
    503: {
        "model": schemas.ErrorResponse,
        "description": "DEM_UNAVAILABLE (tiles not cached and downloads not possible)",
    },
    422: {
        "model": schemas.ErrorResponse,
        "description": "INVALID_REQUEST, INVALID_PARAMS, SCENARIO_INPUT, or a GPX "
        "read error code (EMPTY_FILE, INVALID_XML, INVALID_GPX, ...)",
    },
}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def _error(status: int, code: str, message: str, details: Any = None) -> JSONResponse:
    body: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        body["details"] = details
    return JSONResponse(status_code=status, content={"error": body})


def create_app(data_dir: Path, dem_api_key: str | None = None) -> FastAPI:
    """``dem_api_key`` enables DEM downloads (``POST /tracks/{id}/dem``). It is
    passed in by the entry point, kept in memory, and never returned or logged."""

    def dem_provider(dataset_id: str, allow_download: bool) -> ElevationSampler:
        try:
            return open_dem(
                data_dir / "dem", dataset_id, dem_api_key if allow_download else None
            )
        except ValueError as exc:
            raise ApiError(422, "INVALID_PARAMS", str(exc)) from exc

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.workspace = Workspace(data_dir)
        app.state.dem_provider = dem_provider
        app.state.dem_downloads_available = dem_api_key is not None
        # One enrichment at a time: tiles are written to a shared cache.
        app.state.dem_lock = threading.Lock()
        try:
            yield
        finally:
            app.state.workspace.close()

    app = FastAPI(
        title="ALPHA",
        version=__version__,
        description=(
            "Deterministic analysis of GPX planned routes and recorded activities. "
            "Values are labelled calculated, estimated, observed, or unknown. "
            "ALPHA is not a navigation system and never says a route is safe."
        ),
        lifespan=lifespan,
    )
    _install_error_handlers(app)
    _install_request_log(app)
    _install_routes(app)
    return app


# --- infrastructure -----------------------------------------------------------------


def _install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error(_: Request, exc: ApiError) -> JSONResponse:
        return _error(exc.status, exc.code, exc.message)

    @app.exception_handler(TrackNotFoundError)
    async def not_found(_: Request, exc: TrackNotFoundError) -> JSONResponse:
        return _error(404, "TRACK_NOT_FOUND", str(exc))

    @app.exception_handler(InvalidEditError)
    async def invalid_edit(_: Request, exc: InvalidEditError) -> JSONResponse:
        return _error(409, "INVALID_EDIT", str(exc))

    @app.exception_handler(GpxReadError)
    async def gpx_error(_: Request, exc: GpxReadError) -> JSONResponse:
        status = 413 if exc.code is ReadErrorCode.FILE_TOO_LARGE else 422
        return _error(status, str(exc.code), exc.message)

    @app.exception_handler(DemProfileMissingError)
    async def dem_missing(_: Request, exc: DemProfileMissingError) -> JSONResponse:
        return _error(409, "DEM_PROFILE_MISSING", str(exc))

    @app.exception_handler(DemUnavailableError)
    async def dem_unavailable(_: Request, exc: DemUnavailableError) -> JSONResponse:
        return _error(503, "DEM_UNAVAILABLE", str(exc))

    @app.exception_handler(DemDownloadError)
    async def dem_download(_: Request, exc: DemDownloadError) -> JSONResponse:
        return _error(502, "DEM_DOWNLOAD_FAILED", str(exc))  # key already redacted

    @app.exception_handler(ScenarioInputError)
    async def scenario_input(_: Request, exc: ScenarioInputError) -> JSONResponse:
        return _error(422, "SCENARIO_INPUT", str(exc))

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"loc": list(e.get("loc", ())), "msg": e.get("msg", "")}
            for e in exc.errors()
        ]
        return _error(422, "INVALID_REQUEST", "request validation failed", details)


def _install_request_log(app: FastAPI) -> None:
    """One structured line per request. Logs method, path (IDs only), status,
    and duration; never bodies, query strings, file names, or coordinates."""

    @app.middleware("http")
    async def log_request(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        operation = uuid.uuid4().hex[:12]
        start = time.perf_counter()
        response = await call_next(request)
        logger.info(
            json.dumps(
                {
                    "event": "request",
                    "operation_id": operation,
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": round((time.perf_counter() - start) * 1000, 1),
                }
            )
        )
        response.headers["X-Operation-Id"] = operation
        return response


def _workspace(request: Request) -> Workspace:
    ws: Workspace = request.app.state.workspace
    return ws


WorkspaceDep = Annotated[Workspace, Depends(_workspace)]


def _analysis_params(
    profile_bin_m: Annotated[float, Query()] = _DEFAULTS.profile_bin_m,
    ascent_threshold_m: Annotated[float, Query()] = _DEFAULTS.ascent_threshold_m,
    distance_min_step_m: Annotated[float, Query()] = _DEFAULTS.distance_min_step_m,
    moving_min_speed_m_s: Annotated[float, Query()] = _DEFAULTS.moving_min_speed_m_s,
    moving_window_s: Annotated[float, Query()] = _DEFAULTS.moving_window_s,
    climb_min_gradient_percent: Annotated[
        float, Query()
    ] = _DEFAULTS.climb_min_gradient_percent,
    elevation: Annotated[
        str, Query(description="'recorded' or 'dem:<dataset>' (e.g. dem:COP30)")
    ] = _DEFAULTS.elevation,
) -> AnalysisParams:
    """Analysis assumptions; defaults are documented in DATA_MODEL.md §6."""
    try:
        return AnalysisParams(
            profile_bin_m=profile_bin_m,
            ascent_threshold_m=ascent_threshold_m,
            distance_min_step_m=distance_min_step_m,
            moving_min_speed_m_s=moving_min_speed_m_s,
            moving_window_s=moving_window_s,
            climb_min_gradient_percent=climb_min_gradient_percent,
            elevation=elevation,
        )
    except ValueError as exc:
        raise ApiError(422, "INVALID_PARAMS", str(exc)) from exc


ParamsDep = Annotated[AnalysisParams, Depends(_analysis_params)]


# --- routes ---------------------------------------------------------------------------


def _install_routes(app: FastAPI) -> None:
    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html", media_type="text/html")

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.post(
        "/files",
        tags=["tracks"],
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

    @app.get("/tracks", tags=["tracks"], response_model=list[schemas.TrackListItem])
    def tracks(ws: WorkspaceDep) -> list[dict[str, Any]]:
        return [
            list_item_report(i.record, i.summary, i.dem_datasets)
            for i in list_tracks(ws)
        ]

    @app.get(
        "/tracks/{track_id}",
        tags=["tracks"],
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

    @app.patch(
        "/tracks/{track_id}",
        tags=["tracks"],
        response_model=schemas.TrackRecord,
        responses=ERROR_RESPONSES,
        summary="Correct kind or elevation source, or link to a planned route",
    )
    def edit(
        track_id: int, body: schemas.TrackEdit, ws: WorkspaceDep
    ) -> dict[str, Any]:
        record = edit_track(
            ws,
            track_id,
            kind=body.kind,
            elevation_source=body.elevation_source,
            planned_track_id=body.planned_track_id,
            clear_planned_track=body.clear_planned_track,
        )
        return record_report(record)

    @app.delete(
        "/tracks/{track_id}/source-file",
        tags=["tracks"],
        response_model=schemas.Deletion,
        responses=ERROR_RESPONSES,
        summary="Delete the GPX file containing this track, with all its tracks",
    )
    def delete(
        track_id: int, ws: WorkspaceDep, dry_run: bool = False
    ) -> dict[str, Any]:
        plan = delete_source_of_track(ws, track_id, confirm=not dry_run)
        return {
            "source_file_id": plan.source_file_id,
            "source_filename": plan.source_filename,
            "tracks": [record_report(r) for r in plan.tracks],
            "deleted": plan.deleted,
        }

    @app.get(
        "/comparisons",
        tags=["analysis"],
        response_model=schemas.Comparison,
        responses=ERROR_RESPONSES,
        summary="Compare track b against track a under one parameter set",
    )
    def comparison(
        a: int, b: int, ws: WorkspaceDep, params: ParamsDep
    ) -> dict[str, Any]:
        c = compare_tracks(ws, a, b, params)
        return comparison_report(
            stored_track_ref(c.a.id, c.a.source_filename, c.a.planned_track_id),
            stored_track_ref(c.b.id, c.b.source_filename, c.b.planned_track_id),
            c.result,
            c.dem_a,
            c.dem_b,
        )

    @app.get(
        "/tracks/{track_id}/scenarios",
        tags=["analysis"],
        response_model=schemas.Scenarios,
        responses=ERROR_RESPONSES,
        summary="Estimated duration range (DIN 33466) next to observed times",
    )
    def scenarios(
        track_id: int,
        ws: WorkspaceDep,
        params: ParamsDep,
        horizontal_speed_km_h: float | None = None,
        ascent_rate_m_h: float | None = None,
        descent_rate_m_h: float | None = None,
        break_min: float = 0.0,
        delay_min: float = 0.0,
        speed_multiplier: Annotated[
            list[float] | None,
            Query(description="repeat for a range; default 1.2, 1, 0.8"),
        ] = None,
    ) -> dict[str, Any]:
        multipliers = tuple(speed_multiplier or DEFAULT_SPEED_MULTIPLIERS)
        try:
            assumptions = din_assumptions(
                horizontal_speed_m_h=(
                    None
                    if horizontal_speed_km_h is None
                    else horizontal_speed_km_h * 1000
                ),
                ascent_rate_m_h=ascent_rate_m_h,
                descent_rate_m_h=descent_rate_m_h,
                break_s=break_min * 60,
                delay_s=delay_min * 60,
            )
            if any(k <= 0 for k in multipliers):
                raise ValueError("speed_multiplier values must be > 0")
        except ValueError as exc:
            raise ApiError(422, "INVALID_PARAMS", str(exc)) from exc
        view = estimate_duration(ws, track_id, assumptions, multipliers, params)
        r = view.record
        return scenario_report(
            stored_track_ref(r.id, r.source_filename, r.planned_track_id),
            view.result,
            view.observed,
            view.dem,
        )

    # --- DEM elevation (ADR-012) ---------------------------------------------------

    @app.get("/dem/datasets", tags=["elevation"], response_model=schemas.DemDatasets)
    def dem_datasets(request: Request) -> dict[str, Any]:
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
            "downloads_available": request.app.state.dem_downloads_available,
        }

    @app.post(
        "/tracks/{track_id}/dem",
        tags=["elevation"],
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
        request: Request,
        ws: WorkspaceDep,
        dataset: str = DEFAULT_DATASET,
        refresh: bool = False,
        offline: bool = False,
    ) -> dict[str, Any]:
        provider = request.app.state.dem_provider(dataset, not offline)
        with request.app.state.dem_lock:
            view = enrich_track(ws, track_id, provider, refresh=refresh)
        r = view.record
        return elevation_check_report(
            stored_track_ref(r.id, r.source_filename, r.planned_track_id), view
        )

    @app.get(
        "/tracks/{track_id}/dem/{dataset}",
        tags=["elevation"],
        response_model=schemas.ElevationCheck,
        responses=ERROR_RESPONSES,
        summary="Compare recorded elevation with the stored DEM profile (no network)",
    )
    def elevation(track_id: int, dataset: str, ws: WorkspaceDep) -> dict[str, Any]:
        if dataset not in DATASETS:
            raise ApiError(
                422,
                "INVALID_PARAMS",
                f"unknown DEM dataset {dataset!r}; known: {', '.join(DATASETS)}",
            )
        view = elevation_check(ws, track_id, dataset)
        r = view.record
        return elevation_check_report(
            stored_track_ref(r.id, r.source_filename, r.planned_track_id), view
        )
