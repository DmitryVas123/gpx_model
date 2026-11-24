"""Plain-language explanations of ALPHA's results (Phase 8, ADR-020).

Templates explain every report without any model. An optional language
model may reword them; its text is used only after validation against the
report's facts. Pure: no network, files, or database. A model is supplied
by the caller as a function.
"""

from alpha.explain.explain import (
    SYSTEM_PROMPT,
    Explanation,
    Rewrite,
    RewriteError,
    explain,
)
from alpha.explain.facts import Fact, FactSet
from alpha.explain.templates import BOUNDARY, TOPICS, Statement

__all__ = [
    "BOUNDARY",
    "SYSTEM_PROMPT",
    "TOPICS",
    "Explanation",
    "Fact",
    "FactSet",
    "Rewrite",
    "RewriteError",
    "Statement",
    "explain",
]
