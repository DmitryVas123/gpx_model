"""What every request shares (ADR-011): the workspace, the DEM and model
settings from ``alpha serve``, and the common query dependencies.

``ApiContext`` replaces loose ``app.state`` attributes with one typed object.
The OpenTopography key lives only inside it; it is never returned or logged.
"""

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated

from fastapi import Depends, Query, Request

from alpha.api.errors import invalid_params
from alpha.application.tracks import Workspace
from alpha.dem import DATASETS, ElevationSampler, open_dem
from alpha.domain.analysis import AnalysisParams
from alpha.modelling import BaselineParams


@dataclass
class ApiContext:
    workspace: Workspace
    data_dir: Path
    llm_model: str
    _dem_api_key: str | None = field(repr=False)
    # One enrichment at a time: tiles are written to a shared cache.
    dem_lock: threading.Lock = field(default_factory=threading.Lock)
    # One model call at a time (GPU memory); the model runs on request only.
    llm_lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def dem_downloads_available(self) -> bool:
        return self._dem_api_key is not None

    def dem_sampler(self, dataset_id: str, allow_download: bool) -> ElevationSampler:
        """The sampler for a dataset; downloads only if allowed and a key exists."""
        try:
            return open_dem(
                self.data_dir / "dem",
                dataset_id,
                self._dem_api_key if allow_download else None,
            )
        except ValueError as exc:
            raise invalid_params(str(exc)) from exc


def _context(request: Request) -> ApiContext:
    context: ApiContext = request.app.state.context
    return context


def _workspace(context: Annotated[ApiContext, Depends(_context)]) -> Workspace:
    return context.workspace


ContextDep = Annotated[ApiContext, Depends(_context)]
WorkspaceDep = Annotated[Workspace, Depends(_workspace)]

_DEFAULTS = AnalysisParams()
DEFAULT_EFFORT = BaselineParams().effort  # "normal"


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
        raise invalid_params(str(exc)) from exc


ParamsDep = Annotated[AnalysisParams, Depends(_analysis_params)]


def baseline_params(effort: str) -> BaselineParams:
    """Baseline settings for an ``effort`` query value (422 if unknown)."""
    try:
        return BaselineParams(effort=effort)
    except ValueError as exc:
        raise invalid_params(str(exc)) from exc


def require_dataset(dataset: str) -> str:
    if dataset not in DATASETS:
        raise invalid_params(
            f"unknown DEM dataset {dataset!r}; known: {', '.join(DATASETS)}"
        )
    return dataset
