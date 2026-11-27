"""Plain-language explanations (ADR-020): templates by default; the local
model only on request (``llm=true``), one call at a time."""

from typing import Any, Literal

from fastapi import APIRouter

from alpha.api import schemas
from alpha.api.context import (
    DEFAULT_EFFORT,
    ApiContext,
    ContextDep,
    ParamsDep,
    WorkspaceDep,
    baseline_params,
    require_dataset,
)
from alpha.api.errors import ERROR_RESPONSES, invalid_params
from alpha.api.reports import baseline_json, scenarios_json, stored_elevation_json
from alpha.dem import DEFAULT_DATASET
from alpha.domain.scenarios import din_assumptions
from alpha.explain import explain
from alpha.llm import OllamaRewriter
from alpha.report import explanation_report

router = APIRouter(tags=["explanations"])


def _explained(
    context: ApiContext, topic: str, report: dict[str, Any], llm: bool, language: str
) -> dict[str, Any]:
    try:
        if not llm:
            return explanation_report(explain(topic, report, language=language))
        rewriter = OllamaRewriter(context.llm_model)
        with context.llm_lock:
            result = explain(
                topic, report, rewriter, model=context.llm_model, language=language
            )
    except ValueError as exc:  # e.g. a language the checks do not cover
        raise invalid_params(str(exc)) from exc
    return explanation_report(result, rewriter.notes)


@router.get(
    "/tracks/{track_id}/explanation",
    response_model=schemas.Explanation,
    responses=ERROR_RESPONSES,
    summary="Plain-language explanation of scenarios or the elevation check",
    description=(
        "Template text by default. llm=true asks the local Ollama model to "
        "reword it; its text is used only if every number matches the "
        "report, otherwise the template is returned with the reasons."
    ),
)
def track_explanation(
    track_id: int,
    context: ContextDep,
    ws: WorkspaceDep,
    params: ParamsDep,
    topic: Literal["scenarios", "elevation"],
    llm: bool = False,
    language: str = "English",
    personal: bool = False,
    break_min: float = 0.0,
    dataset: str = DEFAULT_DATASET,
    effort: str = DEFAULT_EFFORT,
) -> dict[str, Any]:
    if topic == "elevation":
        report = stored_elevation_json(ws, track_id, require_dataset(dataset), params)
    else:
        if break_min < 0:
            raise invalid_params("break_min must be >= 0")
        report = scenarios_json(
            ws,
            track_id,
            params,
            assumptions=None if personal else din_assumptions(break_s=break_min * 60),
            baseline=baseline_params(effort),
            multipliers=None,
            break_s=break_min * 60,
        )
    return _explained(context, topic, report, llm, language)


@router.get(
    "/baseline/explanation",
    response_model=schemas.Explanation,
    responses=ERROR_RESPONSES,
    summary="Plain-language explanation of the personal speed models",
)
def baseline_explanation(
    context: ContextDep,
    ws: WorkspaceDep,
    params: ParamsDep,
    llm: bool = False,
    language: str = "English",
    effort: str = DEFAULT_EFFORT,
) -> dict[str, Any]:
    report = baseline_json(ws, params, baseline_params(effort))
    return _explained(context, "baseline", report, llm, language)
