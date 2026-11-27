"""What every request shares (ADR-011): the active profile and its
workspace (ADR-021), the DEM and model settings from ``alpha serve``, and
the common query dependencies.

``ApiContext`` replaces loose ``app.state`` attributes with one typed object.
The OpenTopography key lives only inside it; it is never returned or logged.
"""

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated

from fastapi import Depends, Query, Request

from alpha.api.errors import invalid_params
from alpha.application.profiles import Profile, open_profile
from alpha.application.tracks import Workspace
from alpha.dem import DATASETS, ElevationSampler, open_dem
from alpha.domain.analysis import AnalysisParams
from alpha.modelling import BaselineParams


@dataclass
class ApiContext:
    base_dir: Path  # the data directory; profiles live inside it
    profile: Profile
    workspace: Workspace  # the active profile's database and files
    llm_model: str
    _dem_api_key: str | None = field(repr=False)
    # One enrichment at a time: tiles are written to a shared cache.
    dem_lock: threading.Lock = field(default_factory=threading.Lock)
    # One model call at a time (GPU memory); the model runs on request only.
    llm_lock: threading.Lock = field(default_factory=threading.Lock)
    profile_lock: threading.Lock = field(default_factory=threading.Lock)

    @classmethod
    def open(
        cls, base_dir: Path, profile: str, llm_model: str, dem_api_key: str | None
    ) -> "ApiContext":
        p = open_profile(base_dir, profile)
        return cls(
            base_dir,
            p,
            Workspace(p.data_dir, synthetic=p.synthetic),
            llm_model,
            dem_api_key,
        )

    def switch_profile(self, name: str) -> Profile:
        """Make ``name`` the active profile for all later requests. Raises
        ``ProfileError`` for an unknown profile; nothing changes then."""
        profile = open_profile(self.base_dir, name)
        with self.profile_lock:
            if profile == self.profile:
                return profile
            old = self.workspace
            self.workspace = Workspace(profile.data_dir, synthetic=profile.synthetic)
            self.profile = profile
        old.close()
        return profile

    @property
    def dem_downloads_available(self) -> bool:
        return self._dem_api_key is not None

    def dem_sampler(self, dataset_id: str, allow_download: bool) -> ElevationSampler:
        """The sampler for a dataset; downloads only if allowed and a key exists."""
        try:
            return open_dem(
                self.profile.dem_dir,
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
