"""The local page and the health check."""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

from alpha import __version__

STATIC = Path(__file__).resolve().parents[1] / "static"

router = APIRouter()


@router.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html", media_type="text/html")


@router.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
