"""Uniform error responses: ``{"error": {"code", "message", "details?"}}``
(ADR-011). Domain and application exceptions map to HTTP status codes here,
so route handlers never translate them themselves."""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from alpha.api import schemas
from alpha.application.baseline import BaselineNotFittedError
from alpha.application.profiles import ProfileError
from alpha.application.tracks import (
    DemProfileMissingError,
    InvalidEditError,
    TrackNotFoundError,
)
from alpha.dem import DemDownloadError, DemUnavailableError
from alpha.domain.scenarios import ScenarioInputError
from alpha.gpx.reader import GpxReadError, ReadErrorCode

# Documented on every route that can fail with them (OpenAPI).
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {
        "model": schemas.ErrorResponse,
        "description": "TRACK_NOT_FOUND or PROFILE_NOT_FOUND",
    },
    409: {
        "model": schemas.ErrorResponse,
        "description": "INVALID_EDIT, DEM_PROFILE_MISSING (run enrichment first), "
        "or BASELINE_NOT_FITTED (not enough activities for personal speeds)",
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
    """An error a route raises directly (e.g. invalid parameters)."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def invalid_params(message: str) -> ApiError:
    return ApiError(422, "INVALID_PARAMS", message)


def error_response(
    status: int, code: str, message: str, details: Any = None
) -> JSONResponse:
    body: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        body["details"] = details
    return JSONResponse(status_code=status, content={"error": body})


# Exception type -> (status, code). Messages come from the exception.
_MAPPED: tuple[tuple[type[Exception], int, str], ...] = (
    (TrackNotFoundError, 404, "TRACK_NOT_FOUND"),
    (ProfileError, 404, "PROFILE_NOT_FOUND"),
    (InvalidEditError, 409, "INVALID_EDIT"),
    (DemProfileMissingError, 409, "DEM_PROFILE_MISSING"),
    (BaselineNotFittedError, 409, "BASELINE_NOT_FITTED"),
    (ScenarioInputError, 422, "SCENARIO_INPUT"),
    (DemDownloadError, 502, "DEM_DOWNLOAD_FAILED"),  # the API key is already redacted
    (DemUnavailableError, 503, "DEM_UNAVAILABLE"),
)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error(_: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc.status, exc.code, exc.message)

    for exc_type, status, code in _MAPPED:

        async def mapped(
            _: Request, exc: Exception, status: int = status, code: str = code
        ) -> JSONResponse:
            return error_response(status, code, str(exc))

        app.add_exception_handler(exc_type, mapped)

    @app.exception_handler(GpxReadError)
    async def gpx_error(_: Request, exc: GpxReadError) -> JSONResponse:
        status = 413 if exc.code is ReadErrorCode.FILE_TOO_LARGE else 422
        return error_response(status, str(exc.code), exc.message)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"loc": list(e.get("loc", ())), "msg": e.get("msg", "")}
            for e in exc.errors()
        ]
        return error_response(
            422, "INVALID_REQUEST", "request validation failed", details
        )
