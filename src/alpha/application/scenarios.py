"""Duration scenarios for stored tracks, shown next to observed times.

Scenarios are recomputed on demand and not stored: they are deterministic
given the analysis, the method, and the assumptions (ADR-005).
"""

from dataclasses import dataclass
from enum import StrEnum

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


def estimate_duration(
    ws: Workspace,
    track_id: int,
    assumptions: DinAssumptions,
    speed_multipliers: tuple[float, ...] = DEFAULT_SPEED_MULTIPLIERS,
    params: AnalysisParams | None = None,
) -> ScenarioView:
    """Raises ``ScenarioInputError`` when the track has no usable elevation."""
    p = params or AnalysisParams()
    with ws.db.transaction() as repo:
        record = require_track(repo, track_id)
        _, analysis = analysis_for(repo, track_id, p)
        dem = dem_profile_for(repo, track_id, p)
        result = run_scenarios(analysis, assumptions, speed_multipliers)

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
