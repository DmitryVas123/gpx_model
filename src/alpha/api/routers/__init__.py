"""API routes, one router per topic (ARCHITECTURE.md §7)."""

from alpha.api.routers import analysis, elevation, explanations, meta, tracks

ROUTERS = (
    meta.router,
    tracks.router,
    analysis.router,
    elevation.router,
    explanations.router,
)

__all__ = ["ROUTERS"]
