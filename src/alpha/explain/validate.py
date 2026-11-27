"""Checks for explanation text (ADR-020).

A statement passes only if

-   every fact key it cites exists;
-   every duration in it matches a duration fact (within a minute);
-   every other number in it matches a number of some fact (rounding
    tolerance) or appears in the template draft;
-   it does not touch forbidden topics: safety, risk, weather, snow,
    avalanches, route conditions, or advice to go, continue, or turn back
    (CLAUDE.md §1, §7).
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass

from alpha.explain.facts import FactSet

# Words that must not appear in a model-written explanation. Case-insensitive,
# whole words. English and German (the user may ask for German).
FORBIDDEN = re.compile(
    r"\b("
    r"safe\w*|unsafe|danger\w*|hazard\w*|risk\w*|"
    r"avalanche\w*|weather|forecast\w*|snow\w*|ice|icy|storm\w*|thunder\w*|"
    r"lightning|rain\w*|wind|winds|windy|"
    r"turn back|should (?:not )?(?:go|attempt|continue|start|hike)|"
    r"recommend\w*|advis\w*|"
    r"lawine\w*|wetter|schnee\w*|gewitter\w*|gefahr\w*|gefährlich\w*|umkehren"
    r")\b",
    re.IGNORECASE,
)

_DURATION_PATTERNS = (
    re.compile(r"\b(\d{1,2}):([0-5]\d)\b(?:\s*h\b)?"),  # 3:53 h
    re.compile(
        r"\b(\d{1,2})\s*(?:h|hours?|hrs?|std\.?|stunden?)\s*(?:and\s+|und\s+)?"
        r"(\d{1,2})\s*(?:min|minutes?|minuten)\b",
        re.IGNORECASE,
    ),  # 3 h 53 min
    re.compile(r"\b(\d{1,3})\s*(?:min|minutes?|minuten)\b", re.IGNORECASE),  # 233 min
)
_MINUS = chr(0x2212)  # typographic minus, as models often write it
_FACTOR_SIGN = re.compile(
    "(?<![A-Za-z])[x" + chr(0xD7) + r"](?=\d)"
)  # x1.46 or with the multiplication sign
_NUMBER = re.compile(r"(?<![\w.])[-+" + _MINUS + r"]?\d[\d,]*(?:\.\d+)?")


@dataclass(frozen=True)
class Problem:
    statement: int  # index of the statement
    message: str


def _durations(text: str) -> tuple[list[float], str]:
    """Minutes of every duration in ``text``, and the text without them."""
    minutes: list[float] = []
    for i, pattern in enumerate(_DURATION_PATTERNS):

        def take(m: re.Match[str], i: int = i) -> str:
            if i == 2:
                minutes.append(float(m.group(1)))
            else:
                minutes.append(int(m.group(1)) * 60 + int(m.group(2)))
            return " "

        text = pattern.sub(take, text)
    return minutes, text


def _numbers(text: str) -> list[list[float]]:
    """Each number token, with its possible readings (comma as thousands
    separator or as decimal point)."""
    result = []
    # "x1.46" (or with the multiplication sign) is a factor: free the number
    # from its sign so it is checked like any other number (a letter before
    # a digit would otherwise hide it from the pattern).
    text = _FACTOR_SIGN.sub(" ", text)
    for token in _NUMBER.findall(text):
        token = token.replace(_MINUS, "-").lstrip("+")
        readings = []
        if "," in token:
            readings.append(token.replace(",", ""))
            if token.count(",") == 1 and "." not in token:
                readings.append(token.replace(",", "."))
        else:
            readings.append(token)
        values = []
        for r in readings:
            try:
                values.append(abs(float(r)))
            except ValueError:
                continue
        if values:
            result.append(values)
    return result


def _close(value: float, allowed: Iterable[tuple[float, float]]) -> bool:
    """Within the tolerance of an allowed number (plus float noise)."""
    return any(abs(value - abs(a)) <= tol + 1e-9 for a, tol in allowed)


def check(
    statements: list[tuple[str, tuple[str, ...]]],
    facts: FactSet,
    draft_text: str,
) -> list[Problem]:
    problems: list[Problem] = []
    allowed_minutes = [float(f.value) / 60 for f in facts if f.unit == "duration"]
    allowed_numbers = [n for f in facts for n in f.numbers()]
    for f in facts:  # numbers inside text facts, e.g. "DIN 33466": exact
        if f.unit == "text":
            allowed_numbers += [
                (abs(float(x)), 0.0) for x in re.findall(r"\d+(?:\.\d+)?", str(f.value))
            ]
    draft_minutes, draft_rest = _durations(draft_text)
    allowed_minutes += draft_minutes
    # Numbers as written in the draft (e.g. "95 %", "model 2"): exact.
    allowed_numbers += [(v, 0.0) for values in _numbers(draft_rest) for v in values]

    for i, (text, keys) in enumerate(statements):
        for key in keys:
            if key not in facts:
                problems.append(Problem(i, f"cites unknown fact '{key}'"))
        forbidden = FORBIDDEN.search(text)
        if forbidden:
            problems.append(
                Problem(
                    i, f"mentions '{forbidden.group(0)}', which ALPHA does not judge"
                )
            )
        minutes, rest = _durations(text)
        for m in minutes:
            if not any(abs(m - a) <= 1.0 for a in allowed_minutes):
                problems.append(
                    Problem(i, f"duration of {m:.0f} min is not in the report")
                )
        for readings in _numbers(rest):
            if not any(_close(v, allowed_numbers) for v in readings):
                problems.append(
                    Problem(i, f"number {readings[0]:g} is not in the report")
                )
    return problems


def missing_facts(
    texts: list[str], facts: FactSet, required: Iterable[str]
) -> list[str]:
    """Labels of required numeric facts that no text states (in any accepted
    form). Catches a model that drops a value while everything it does say
    is correct."""
    minutes: list[float] = []
    numbers: list[float] = []
    for text in texts:
        m, rest = _durations(text)
        minutes += m
        numbers += [v for values in _numbers(rest) for v in values]
    missing = []
    for key in dict.fromkeys(required):
        fact = facts.get(key)
        if fact is None or fact.unit == "text":
            continue
        if fact.unit == "duration":
            found = any(abs(m - float(fact.value) / 60) <= 1.0 for m in minutes)
        else:
            found = any(_close(n, fact.numbers()) for n in numbers)
        if not found:
            missing.append(fact.label)
    return missing


def citation_problems(
    statements: list[tuple[str, tuple[str, ...]]],
    facts: FactSet,
    draft: list[tuple[str, tuple[str, ...]]],
) -> list[Problem]:
    """Each statement must state the numeric facts it cites, and every number
    in it must belong to a cited fact (or be part of the template's own
    wording, such as "Model 1" or "95 %").

    Catches a correct number attached to the wrong meaning, e.g. the climbing
    time presented as the moving time, as long as the model cites what it
    means (the prompt asks for that)."""
    literal_numbers, literal_minutes = _literals(draft, facts)
    text_numbers = [
        (abs(float(x)), 0.0)
        for f in facts
        if f.unit == "text"
        for x in re.findall(r"\d+(?:\.\d+)?", str(f.value))
    ]
    problems: list[Problem] = []
    for i, (text, keys) in enumerate(statements):
        cited = [facts[k] for k in keys if k in facts and facts[k].unit != "text"]
        cited_minutes = [float(f.value) / 60 for f in cited if f.unit == "duration"]
        cited_numbers = [n for f in cited if f.unit != "duration" for n in f.numbers()]
        minutes, rest = _durations(text)
        numbers = [values for values in _numbers(rest)]

        for m in minutes:
            if any(abs(m - a) <= 1.0 for a in cited_minutes + literal_minutes):
                continue
            owner = _owner_of_minutes(m, facts)
            if owner:
                problems.append(
                    Problem(
                        i,
                        f"states {m:.0f} min, which is the {owner}, without citing it",
                    )
                )
        for readings in numbers:
            allowed = cited_numbers + literal_numbers + text_numbers
            if any(_close(v, allowed) for v in readings):
                continue
            owner = _owner_of_number(readings, facts)
            if owner:
                problems.append(
                    Problem(
                        i,
                        f"states {readings[0]:g}, which is the {owner}, "
                        "without citing it",
                    )
                )
        for f in cited:
            if f.unit == "duration":
                stated = any(abs(m - float(f.value) / 60) <= 1.0 for m in minutes)
            else:
                stated = any(_close(v, f.numbers()) for vs in numbers for v in vs)
            if not stated:
                problems.append(
                    Problem(i, f"cites the {f.label} but does not state it")
                )
    return problems


def _literals(
    draft: list[tuple[str, tuple[str, ...]]], facts: FactSet
) -> tuple[list[tuple[float, float]], list[float]]:
    """Numbers in the draft that are template wording, not fact values: the
    draft text with the displays of its cited facts removed."""
    numbers: list[tuple[float, float]] = []
    minutes: list[float] = []
    for text, keys in draft:
        for k in keys:
            if k in facts:
                text = text.replace(facts[k].display(), " ")
        m, rest = _durations(text)
        minutes += m
        numbers += [(v, 0.0) for values in _numbers(rest) for v in values]
    return numbers, minutes


def _owner_of_minutes(m: float, facts: FactSet) -> str | None:
    for f in facts:
        if f.unit == "duration" and abs(m - float(f.value) / 60) <= 1.0:
            return f.label
    return None


def _owner_of_number(readings: list[float], facts: FactSet) -> str | None:
    for f in facts:
        if f.unit not in ("text", "duration") and any(
            _close(v, f.numbers()) for v in readings
        ):
            return f.label
    return None
