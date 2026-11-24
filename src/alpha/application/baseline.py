"""Personal speed baseline from stored activities (Phase 7, ADR-016).

Collects one sample per eligible activity, with the elevation series the
caller chose, and hands them to ``alpha.modelling``. Every activity that is
left out is reported with the reason, never dropped silently.
"""

import math
from dataclasses import dataclass

from alpha.application.tracks import (
    DemProfileMissingError,
    Workspace,
    analysis_for,
)
from alpha.domain.analysis import AnalysisParams
from alpha.domain.scenarios import DinAssumptions
from alpha.domain.track import TrackKind
from alpha.modelling import (
    ActivitySample,
    BaselineParams,
    EffortGroup,
    MultiplierFit,
    PaceVsModel,
    SplitFit,
    effort_groups,
    fit_speed_multiplier,
    fit_split_speeds,
    pace_vs_models,
)
from alpha.modelling.baseline import t975
from alpha.persistence import Repository, TrackRecord


@dataclass(frozen=True)
class ExcludedActivity:
    track_id: int
    name: str | None
    reason: str


@dataclass(frozen=True)
class BaselineView:
    fit: MultiplierFit  # model 1: one speed multiplier (ADR-016)
    split: SplitFit  # model 2: flat and vertical factors (ADR-017)
    excluded: tuple[ExcludedActivity, ...]
    params: AnalysisParams  # the analyses the samples came from
    baseline_params: BaselineParams
    # Effort (ADR-018): observed groups over all usable activities, and the
    # activities outside the fitted selection against the fitted models.
    effort_groups: tuple[EffortGroup, ...] = ()
    outside_effort: tuple[PaceVsModel, ...] = ()


def personal_baseline(
    ws: Workspace,
    params: AnalysisParams | None = None,
    baseline_params: BaselineParams | None = None,
) -> BaselineView:
    p = params or AnalysisParams()
    bp = baseline_params or BaselineParams()
    samples: list[ActivitySample] = []
    other_effort: list[ActivitySample] = []
    excluded: list[ExcludedActivity] = []
    with ws.db.transaction() as repo:
        for record in repo.list_tracks():
            if record.kind is not TrackKind.ACTIVITY:
                continue  # planned routes and unknowns are not observations
            outcome = _sample(repo, record, p, bp)
            if isinstance(outcome, str):
                excluded.append(ExcludedActivity(record.id, record.name, outcome))
            elif outcome.effort not in bp.efforts:
                other_effort.append(outcome)
                excluded.append(
                    ExcludedActivity(
                        record.id,
                        record.name,
                        f"effort {outcome.effort or 'not stated'} (fitting "
                        f"'{bp.effort}' efforts; see the effort section)",
                    )
                )
            else:
                samples.append(outcome)
    one = fit_speed_multiplier(samples, bp)
    two = fit_split_speeds(samples, bp)
    return BaselineView(
        one,
        two,
        tuple(excluded),
        p,
        bp,
        effort_groups(samples + other_effort, bp.reference),
        pace_vs_models(other_effort, one.assumptions, two.assumptions),
    )


def _sample(
    repo: Repository, record: TrackRecord, p: AnalysisParams, bp: BaselineParams
) -> ActivitySample | str:
    """The activity's sample, or why it cannot be used."""
    if record.activity_type not in bp.activity_types:
        return (
            f"activity type {record.activity_type or 'unknown'} "
            f"(used: {', '.join(bp.activity_types)})"
        )
    try:
        summary = analysis_for(repo, record.id, p)[1].summary
    except DemProfileMissingError as exc:
        return f"no {exc.dataset_id} elevation stored (alpha enrich)"
    if summary.moving_time_s is None:
        return "no timestamps, so no moving time"
    if summary.moving_time_s < bp.min_moving_s:
        return (
            f"moving time {summary.moving_time_s / 60:.0f} min "
            f"< {bp.min_moving_s / 60:.0f} min"
        )
    if summary.ascent_m is None or summary.descent_m is None:
        return "ascent unknown (incomplete elevation)"
    return ActivitySample(
        track_id=record.id,
        group=record.planned_track_id or record.id,
        name=record.name,
        started_at_utc=record.started_at_utc,
        distance_m=summary.distance_m,
        ascent_m=summary.ascent_m,
        descent_m=summary.descent_m,
        moving_time_s=summary.moving_time_s,
        effort=record.effort,
    )


# --- personal assumptions for scenarios (Phase 7, step 4; ADR-019) -------------------


class BaselineNotFittedError(Exception):
    """Personal assumptions were requested but no model is fitted yet."""


@dataclass(frozen=True)
class PersonalAssumptions:
    """Fitted speeds chosen for a scenario, with what they rest on."""

    assumptions: DinAssumptions  # source fitted; breaks and delay 0
    model: str  # "multiplier" (model 1) or "split" (model 2)
    n_activities: int
    n_routes: int
    effort: str  # the effort selection the model was fitted on
    median_abs_error: float  # leave one route out
    # Speed multipliers (faster, 1, slower) from the 95 % range for a single
    # new hike: the user's own variation, not a fixed +/- 20 %.
    range_multipliers: tuple[float, float, float]
    fitted_track_ids: tuple[int, ...]


def personal_assumptions(view: BaselineView) -> PersonalAssumptions:
    """The fitted model with the smaller leave-one-route-out error (model 1
    on a tie: simpler). Raises ``BaselineNotFittedError`` if none is fitted."""
    one, two = view.fit, view.split
    candidates: list[tuple[float, int, str]] = []
    if one.validation is not None and one.assumptions is not None:
        candidates.append((one.validation.median_abs_error, 1, "multiplier"))
    if two.validation is not None and two.assumptions is not None:
        candidates.append((two.validation.median_abs_error, 2, "split"))
    if not candidates:
        raise BaselineNotFittedError(
            f"no personal model is fitted yet: {one.n_groups} of {one.min_groups} "
            f"routes for model 1 (effort '{view.baseline_params.effort}', elevation "
            f"{view.params.elevation}); see 'alpha baseline'"
        )
    error, _, model = min(candidates)
    if model == "multiplier":
        assert one.assumptions and one.multiplier and one.single_interval
        lo, hi = one.single_interval
        k = one.multiplier
        chosen, n_act, n_routes = one.assumptions, one.n_activities, one.n_groups
        multipliers = (hi / k, 1.0, lo / k)
        track_ids = tuple(r.sample.track_id for r in one.samples)
    else:
        assert two.assumptions and two.residual_spread is not None
        n = two.n_groups
        factor = math.exp(t975(n - 2) * two.residual_spread * math.sqrt(1 + 1 / n))
        chosen, n_act, n_routes = two.assumptions, two.n_activities, n
        multipliers = (factor, 1.0, 1 / factor)
        track_ids = tuple(r.sample.track_id for r in two.samples)
    return PersonalAssumptions(
        assumptions=chosen,
        model=model,
        n_activities=n_act,
        n_routes=n_routes,
        effort=view.baseline_params.effort,
        median_abs_error=error,
        range_multipliers=multipliers,
        fitted_track_ids=track_ids,
    )
