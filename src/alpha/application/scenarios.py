"""Duration scenarios for stored tracks, shown next to observed times.

Scenarios are recomputed on demand and not stored: they are deterministic
given the analysis, the method, and the assumptions (ADR-005).
"""

import dataclasses
from dataclasses import dataclass
from enum import StrEnum

from alpha.application.baseline import (
    PersonalAssumptions,
    personal_assumptions,
    personal_baseline,
)
from alpha.application.tracks import (
    Workspace,
    analysis_for,
    dem_profile_for,
    require_track,
)
from alpha.domain.analysis import AnalysisParams
from alpha.domain.scenarios import (
    DEFAULT_SPEED_MULTIPLIERS,
    DinAssumptions,
    ScenarioResult,
    implied_speed_multiplier,
    run_scenarios,
    scenario_inputs,
)
from alpha.domain.track import TrackKind
from alpha.modelling import BaselineParams
from alpha.persistence import DemProfileRecord, TrackRecord


class ObservedRelation(StrEnum):
    SAME_TRACK = "same track"  # the estimated track is itself an activity
    LINKED_ACTIVITY = "linked activity"  # an activity that followed this plan


@dataclass(frozen=True)
class ObservedTime:
    """Recorded times of an activity: observed timestamps, calculated metrics."""

    track_id: int
    name: str | None
    relation: ObservedRelation
    moving_time_s: float | None
    elapsed_time_s: float | None
    # Speed multiplier at which the method, applied to this activity's own
    # distance and ascent, matches its moving time. A single reference point,
    # not a personal model.
    implied_speed_multiplier: float | None


@dataclass(frozen=True)
class ScenarioView:
    record: TrackRecord
    result: ScenarioResult
    observed: tuple[ObservedTime, ...]
    dem: DemProfileRecord | None  # provenance when DEM elevation was used
    personal: PersonalAssumptions | None = None  # set for fitted assumptions


def estimate_duration(
    ws: Workspace,
    track_id: int,
    assumptions: DinAssumptions,
    speed_multipliers: tuple[float, ...] = DEFAULT_SPEED_MULTIPLIERS,
    params: AnalysisParams | None = None,
    extra_notes: tuple[str, ...] = (),
) -> ScenarioView:
    """Raises ``ScenarioInputError`` when the track has no usable elevation."""
    p = params or AnalysisParams()
    with ws.db.transaction() as repo:
        record = require_track(repo, track_id)
        _, analysis = analysis_for(repo, track_id, p)
        dem = dem_profile_for(repo, track_id, p)
        result = run_scenarios(
            analysis, assumptions, speed_multipliers, extra_notes=extra_notes
        )

        sources: list[tuple[TrackRecord, ObservedRelation]] = []
        if record.kind is TrackKind.ACTIVITY:
            sources.append((record, ObservedRelation.SAME_TRACK))
        sources += [
            (other, ObservedRelation.LINKED_ACTIVITY)
            for other in repo.list_tracks()
            if other.planned_track_id == track_id
        ]
        observed = []
        for other, relation in sources:
            summary = analysis_for(repo, other.id, p)[1].summary
            # Use the activity's own distance and ascent: a linked activity
            # may have taken a different path than the plan.
            implied = None
            if summary.moving_time_s is not None and summary.ascent_m is not None:
                implied = implied_speed_multiplier(
                    scenario_inputs(summary), assumptions, summary.moving_time_s
                )
            observed.append(
                ObservedTime(
                    track_id=other.id,
                    name=other.name,
                    relation=relation,
                    moving_time_s=summary.moving_time_s,
                    elapsed_time_s=summary.elapsed_time_s,
                    implied_speed_multiplier=implied,
                )
            )
    return ScenarioView(record, result, tuple(observed), dem)


def estimate_personal(
    ws: Workspace,
    track_id: int,
    params: AnalysisParams | None = None,
    baseline_params: BaselineParams | None = None,
    break_s: float = 0.0,
    delay_s: float = 0.0,
    speed_multipliers: tuple[float, ...] | None = None,
) -> ScenarioView:
    """Scenarios with fitted personal speeds (ADR-019). The baseline is fitted
    with the same analysis params (the same elevation series) as the estimate.
    Raises ``BaselineNotFittedError`` instead of falling back to a preset."""
    p = params or AnalysisParams()
    personal = personal_assumptions(personal_baseline(ws, p, baseline_params))
    assumptions = dataclasses.replace(
        personal.assumptions, break_s=break_s, delay_s=delay_s
    )
    notes = [
        f"Personal speeds: model '{personal.model}', fitted on "
        f"{personal.n_activities} activities on {personal.n_routes} routes "
        f"(effort '{personal.effort}'); leave-one-route-out error median "
        f"{personal.median_abs_error:.0%}."
    ]
    if speed_multipliers is not None and len(speed_multipliers) > 1:
        notes.append(
            f"The range ({min(speed_multipliers):.3g}x to "
            f"{max(speed_multipliers):.3g}x your speed) shows sensitivity to pace "
            "only, not to delays or conditions."
        )
    if speed_multipliers is None:
        notes.append(
            "The faster/slower range is your own variation between hikes (95 %, "
            "one new hike), not a fixed percentage."
        )
    if track_id in personal.fitted_track_ids:
        notes.append(
            "This activity is part of the fit, so its observed time is not an "
            "independent check of the estimate."
        )
    view = estimate_duration(
        ws,
        track_id,
        assumptions,
        speed_multipliers or personal.range_multipliers,
        p,
        extra_notes=tuple(notes),
    )
    return dataclasses.replace(view, personal=personal)
