"""Comparisons, duration scenarios, and the personal speed baseline."""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query

from alpha.api import schemas
from alpha.api.context import (
    DEFAULT_EFFORT,
    ParamsDep,
    WorkspaceDep,
    baseline_params,
)
from alpha.api.errors import ERROR_RESPONSES, invalid_params
from alpha.api.reports import baseline_json, comparison_json, scenarios_json
from alpha.domain.scenarios import din_assumptions

router = APIRouter(tags=["analysis"])

EffortQuery = Annotated[
    str, Query(description="personal only: normal, hard, easy, or all")
]


@router.get(
    "/comparisons",
    response_model=schemas.Comparison,
    responses=ERROR_RESPONSES,
    summary="Compare track b against track a under one parameter set",
)
def comparison(a: int, b: int, ws: WorkspaceDep, params: ParamsDep) -> dict[str, Any]:
    return comparison_json(ws, a, b, params)


@router.get(
    "/tracks/{track_id}/scenarios",
    response_model=schemas.Scenarios,
    responses=ERROR_RESPONSES,
    summary="Estimated duration range (DIN 33466) next to observed times",
)
def scenarios(
    track_id: int,
    ws: WorkspaceDep,
    params: ParamsDep,
    horizontal_speed_km_h: float | None = None,
    ascent_rate_m_h: float | None = None,
    descent_rate_m_h: float | None = None,
    break_min: float = 0.0,
    delay_min: float = 0.0,
    speed_multiplier: Annotated[
        list[float] | None,
        Query(
            description="repeat for a range; default 1.2, 1, 0.8 (personal: "
            "your own 95 % range)"
        ),
    ] = None,
    assumptions_source: Annotated[
        Literal["standard", "personal"],
        Query(
            alias="assumptions",
            description="standard: DIN 33466 or the speeds given; personal: "
            "fitted from your activities (409 if not fitted yet)",
        ),
    ] = "standard",
    effort: EffortQuery = DEFAULT_EFFORT,
) -> dict[str, Any]:
    personal = assumptions_source == "personal"
    speeds = (horizontal_speed_km_h, ascent_rate_m_h, descent_rate_m_h)
    if personal and any(v is not None for v in speeds):
        raise invalid_params("personal assumptions use fitted speeds; omit the speeds")
    if speed_multiplier and any(k <= 0 for k in speed_multiplier):
        raise invalid_params("speed_multiplier values must be > 0")
    if break_min < 0 or delay_min < 0:
        raise invalid_params("breaks and delay must be >= 0")
    try:
        assumptions = (
            None
            if personal
            else din_assumptions(
                horizontal_speed_m_h=(
                    None
                    if horizontal_speed_km_h is None
                    else horizontal_speed_km_h * 1000
                ),
                ascent_rate_m_h=ascent_rate_m_h,
                descent_rate_m_h=descent_rate_m_h,
                break_s=break_min * 60,
                delay_s=delay_min * 60,
            )
        )
    except ValueError as exc:
        raise invalid_params(str(exc)) from exc
    return scenarios_json(
        ws,
        track_id,
        params,
        assumptions=assumptions,
        baseline=baseline_params(effort),
        multipliers=tuple(speed_multiplier) if speed_multiplier else None,
        break_s=break_min * 60,
        delay_s=delay_min * 60,
    )


@router.get(
    "/baseline",
    response_model=schemas.Baseline,
    responses=ERROR_RESPONSES,
    summary="Personal speeds fitted from your activities (two models)",
)
def baseline(
    ws: WorkspaceDep,
    params: ParamsDep,
    effort: Annotated[
        str, Query(description="normal (default), hard, easy, or all")
    ] = DEFAULT_EFFORT,
) -> dict[str, Any]:
    return baseline_json(ws, params, baseline_params(effort))
