"""The local page, the health check, and the local profiles (ADR-021)."""

from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import FileResponse

from alpha import __version__
from alpha.api import schemas
from alpha.api.context import ContextDep
from alpha.api.errors import ERROR_RESPONSES
from alpha.application.profiles import list_profiles
from alpha.report import profiles_report

STATIC = Path(__file__).resolve().parents[1] / "static"

router = APIRouter()


@router.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html", media_type="text/html")


@router.get("/health", tags=["meta"], response_model=schemas.Health)
def health(context: ContextDep) -> dict[str, Any]:
    return {
        "status": "ok",
        "version": __version__,
        "profile": context.profile.name,
        "synthetic_data": context.profile.synthetic,
    }


@router.get("/profiles", tags=["meta"], response_model=schemas.Profiles)
def profiles(context: ContextDep) -> dict[str, Any]:
    return profiles_report(list_profiles(context.base_dir), context.profile)


@router.put(
    "/profile",
    tags=["meta"],
    response_model=schemas.Profiles,
    responses=ERROR_RESPONSES,
    summary="Switch the active local profile (create profiles with the CLI)",
)
def select_profile(
    selection: schemas.ProfileSelection, context: ContextDep
) -> dict[str, Any]:
    context.switch_profile(selection.name)
    return profiles_report(list_profiles(context.base_dir), context.profile)
