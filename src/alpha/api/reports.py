"""Use case → JSON report, for reports that several routes return or
explain. JSON itself is still built only in ``alpha.report``; this module
only calls the use case and passes its result on."""

from typing import Any

from alpha.application.baseline import personal_baseline
from alpha.application.elevation import ElevationView, elevation_check
from alpha.application.scenarios import (
    ScenarioView,
    estimate_duration,
    estimate_personal,
)
from alpha.application.tracks import Workspace, compare_tracks
from alpha.domain.analysis import AnalysisParams
from alpha.domain.scenarios import DEFAULT_SPEED_MULTIPLIERS, DinAssumptions
from alpha.modelling import BaselineParams
from alpha.report import (
    baseline_report,
    comparison_report,
    elevation_check_report,
    scenario_report,
    stored_track_ref,
)


def comparison_json(
    ws: Workspace, a: int, b: int, params: AnalysisParams
) -> dict[str, Any]:
    c = compare_tracks(ws, a, b, params)
    return comparison_report(
        stored_track_ref(c.a.id, c.a.source_filename, c.a.planned_track_id),
        stored_track_ref(c.b.id, c.b.source_filename, c.b.planned_track_id),
        c.result,
        c.dem_a,
        c.dem_b,
    )


def scenarios_json(
    ws: Workspace,
    track_id: int,
    params: AnalysisParams,
    *,
    assumptions: DinAssumptions | None,
    baseline: BaselineParams,
    multipliers: tuple[float, ...] | None,
    break_s: float = 0.0,
    delay_s: float = 0.0,
) -> dict[str, Any]:
    """With ``assumptions`` the given (standard or user) speeds; without,
    personal speeds fitted with ``baseline`` (raises if none is fitted)."""
    view: ScenarioView
    if assumptions is None:
        view = estimate_personal(
            ws,
            track_id,
            params,
            baseline,
            break_s=break_s,
            delay_s=delay_s,
            speed_multipliers=multipliers,
        )
    else:
        view = estimate_duration(
            ws,
            track_id,
            assumptions,
            multipliers or DEFAULT_SPEED_MULTIPLIERS,
            params,
        )
    r = view.record
    return scenario_report(
        stored_track_ref(r.id, r.source_filename, r.planned_track_id),
        view.result,
        view.observed,
        view.dem,
        view.personal,
    )


def elevation_json(view: ElevationView) -> dict[str, Any]:
    r = view.record
    return elevation_check_report(
        stored_track_ref(r.id, r.source_filename, r.planned_track_id), view
    )


def stored_elevation_json(
    ws: Workspace, track_id: int, dataset: str, params: AnalysisParams | None = None
) -> dict[str, Any]:
    """The elevation check from the stored profile (no network)."""
    return elevation_json(elevation_check(ws, track_id, dataset, params))


def baseline_json(
    ws: Workspace, params: AnalysisParams, baseline: BaselineParams
) -> dict[str, Any]:
    return baseline_report(personal_baseline(ws, params, baseline))
