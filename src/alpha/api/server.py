"""Run the API with uvicorn (``alpha serve``)."""

import logging
from pathlib import Path

import uvicorn

from alpha.api.app import create_app
from alpha.application.profiles import DEFAULT_PROFILE
from alpha.llm import DEFAULT_MODEL


def run(
    data_dir: Path,
    host: str = "127.0.0.1",
    port: int = 8000,
    dem_api_key: str | None = None,
    llm_model: str | None = None,
    profile: str = DEFAULT_PROFILE,
) -> None:
    """Serve on localhost by default: the data is personal (ADR-011)."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    uvicorn.run(
        create_app(data_dir, dem_api_key, llm_model or DEFAULT_MODEL, profile),
        host=host,
        port=port,
        access_log=False,
    )
