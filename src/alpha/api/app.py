"""HTTP API (ARCHITECTURE.md §7, ADR-011).

``create_app`` wires the shared context, error handlers, request log, and
the routers. Route handlers live in ``alpha.api.routers`` and only translate
HTTP to use cases; JSON is built by ``alpha.report`` and validated against
``alpha.api.schemas``.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from alpha import __version__
from alpha.api.context import ApiContext
from alpha.api.errors import install_error_handlers
from alpha.api.request_log import install_request_log
from alpha.api.routers import ROUTERS
from alpha.application.tracks import Workspace
from alpha.llm import DEFAULT_MODEL


def create_app(
    data_dir: Path,
    dem_api_key: str | None = None,
    llm_model: str = DEFAULT_MODEL,
) -> FastAPI:
    """``dem_api_key`` enables DEM downloads (``POST /tracks/{id}/dem``) and
    ``llm_model`` names the local Ollama model. Both are passed in by the
    entry point; the key is kept in memory and never returned or logged."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        context = ApiContext(
            workspace=Workspace(data_dir),
            data_dir=data_dir,
            llm_model=llm_model,
            _dem_api_key=dem_api_key,
        )
        app.state.context = context
        try:
            yield
        finally:
            context.workspace.close()

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
    install_error_handlers(app)
    install_request_log(app)
    for router in ROUTERS:
        app.include_router(router)
    return app
