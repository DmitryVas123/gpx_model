"""Checks against adversarial model text (ADR-020, adversarial test set).

``validate.check`` and ``validate.citation_problems`` verify that numbers
come from the report. These guards cover what numbers alone cannot:

-   **units:** a number written with a unit must match a fact of that unit
    ("1377 km of ascent" is not the 1377 m ascent);
-   **number words:** quantities spelled out ("two hours", "half an hour")
    escape number checks, so they are rejected unless the template uses
    the same words;
-   **anchors:** a number must sit in its clause next to words of its own
    meaning, so swapped values ("18 m of ascent and 1377 m of descent")
    fail;
-   **content:** advice, suitability, conditions, false certainty, links,
    markup, and instructions, in English and German. Words that the
    template itself uses are allowed;
-   **provenance:** "measured" or "recorded" may describe only observed
    or calculated values, never estimates or fitted speeds;
-   **pairs** (comparisons): a value of track A or B must stand after its
    own letter, and a difference must keep its direction ("less" stays
    "less"), so swapped tracks or a flipped difference fail.

All rules are deterministic and work on a statement's text and its cited
facts. They can still be fooled by text that is wrong in meaning but uses
the right words and numbers; the template stays the default for that
reason.
"""

import re
from dataclasses import dataclass

from alpha.explain.facts import Fact, FactSet

# --- content ---------------------------------------------------------------------

_CONTENT = re.compile(
    r"\b("
    # advice and go / no-go
    r"should|must|ought|need to|have to|make sure|be sure to|remember to|"
    r"don'?t forget|bring|pack|start early|leave early|before dark|daylight|"
    r"consider|suggest\w*|try to|go for it|turn around|give up|"
    # suitability and difficulty
    r"fine for you|suitable|unsuitable|easy|easily|difficult\w*|challenging|"
    r"technical\w*|exposed|exposure|scrambl\w*|beginners?|experienced|"
    r"fit enough|worry|manageable|doable|enjoy\w*|"
    # judgements between routes (comparisons invite them)
    r"easier|easiest|harder|hardest|tougher|toughest|demanding|strenuous|"
    r"better|worse|best|worst|prefer\w*|choice|choose|"
    # conditions
    r"conditions?|summer|winter|spring|autumn|seasons?|dark|night|"
    r"visibility|fog\w*|slippery|wet|mud\w*|"
    # false certainty
    r"exactly|precisely|guarantee\w*|certain(?:ly)?|definitely|for sure|"
    r"will (?:take|need|be)|always|never|"
    # German
    r"sicher\w*|einfach\w*|leicht\w*|schwierig\w*|schwer\w*|empfehl\w*|solltest|"
    r"besser|schlechter|anstrengend\w*|"
    r"sollten|musst|müssen|bedingungen|sommer|winter|garantiert|genau|"
    # output channel
    r"ignore|instructions?|system prompt|prompt"
    r")\b",
    re.IGNORECASE,
)
_CHANNEL = re.compile(r"https?://|www\.|<\s*/?\s*[a-z!]|\]\(|```", re.IGNORECASE)

_NUMBER_WORDS = re.compile(
    r"\b("
    r"two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|"
    r"twenty|thirty|forty|fifty|sixty|hundred|thousand|half|quarter|double|"
    r"twice|triple|dozen|"
    r"(?:an?|one|a few|several|a couple of)\s+(?:hours?|minutes?|kilomet\w*|"
    r"met(?:er|re)s?)|"
    r"zwei|drei|vier|fünf|sechs|sieben|acht|neun|zehn|halbe?|viertel|doppelt"
    r")\b",
    re.IGNORECASE,
)

_OBSERVATION = re.compile(
    r"\b(measured|recorded|observed|actual|tracked|gemessen|aufgezeichnet)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Finding:
    statement: int
    message: str


def content_problems(texts: list[str], draft_text: str) -> list[Finding]:
    """Forbidden content, links and markup, and spelled-out quantities. A
    word that the template's own draft contains is allowed."""
    draft = draft_text.lower()
    found: list[Finding] = []
    for i, text in enumerate(texts):
        if _CHANNEL.search(text):
            found.append(Finding(i, "contains a link or markup"))
        for pattern, what in ((_CONTENT, "says"), (_NUMBER_WORDS, "spells out")):
            for m in pattern.finditer(text):
                if m.group(0).lower() not in draft:
                    found.append(
                        Finding(i, f"{what} '{m.group(0)}', which is not in the report")
                    )
                    break
    return found


# --- units ---------------------------------------------------------------------------

# A number followed by a unit. Longest units first.
_WITH_UNIT = re.compile(
    r"(?<![\w.])(?P<num>\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<unit>km/h|m/h|kilomet(?:er|re)s?|km|met(?:er|re)s?|m|%|percent|"
    r"prozent|deg(?:rees?)?|°)(?![\w/])",
    re.IGNORECASE,
)
_UNIT = {
    "km/h": "km/h",
    "m/h": "m/h",
    "km": "km",
    "kilometer": "km",
    "kilometers": "km",
    "kilometre": "km",
    "kilometres": "km",
    "m": "m",
    "meter": "m",
    "meters": "m",
    "metre": "m",
    "metres": "m",
    "%": "%",
    "percent": "%",
    "prozent": "%",
    "deg": "deg",
    "degree": "deg",
    "degrees": "deg",
    "°": "deg",
}


def _readings(token: str) -> list[float]:
    values = [token.replace(",", "")]
    if token.count(",") == 1 and "." not in token:
        values.append(token.replace(",", "."))
    return [abs(float(v)) for v in values]


def _forms(fact: Fact, unit: str) -> list[tuple[float, float]]:
    """How ``fact`` may be written with ``unit``: (value, tolerance)."""
    v = fact.value
    if isinstance(v, str):
        return []
    match (unit, fact.unit):
        case ("km/h", "km/h"):
            return [(v / 1000, 0.05)]
        case ("m/h", "m/h"):
            return [(v, 0.5)]
        case ("km", "km"):
            return [(v / 1000, 0.05)]
        case ("m", "m") | ("m", "km"):
            return [(v, 0.5)]
        case ("%", "percent"):
            return [(v * 100, 0.5)]
        case ("%", "factor"):
            return [((v - 1) * 100, 0.5), (abs(1 - 1 / v) * 100 if v else 0.0, 0.5)]
        case ("deg", "deg"):
            return [(v, 0.5)]
        case _:
            return []


def _unit_literals(draft_text: str) -> set[tuple[float, str]]:
    return {
        (round(r, 6), _UNIT[m.group("unit").lower()])
        for m in _WITH_UNIT.finditer(draft_text)
        for r in _readings(m.group("num"))
    }


def unit_problems(
    statements: list[tuple[str, tuple[str, ...]]], facts: FactSet, draft_text: str
) -> list[Finding]:
    literals = _unit_literals(draft_text)
    found: list[Finding] = []
    for i, (text, _keys) in enumerate(statements):
        for m in _WITH_UNIT.finditer(text):
            unit = _UNIT[m.group("unit").lower()]
            readings = _readings(m.group("num"))
            if any((round(r, 6), unit) in literals for r in readings):
                continue
            # A number written with decimals claims that precision: "10.46 m"
            # may not stand for an 11 m value, while "11 m" may.
            token = m.group("num").replace(",", ".")
            decimals = len(token.split(".")[1]) if "." in token else 0
            written = 0.5 * 10**-decimals if decimals else float("inf")
            ok = any(
                abs(r - value) <= min(tol, written) + 1e-9
                for f in facts
                for value, tol in _forms(f, unit)
                for r in readings
            )
            if not ok:
                found.append(
                    Finding(i, f"'{m.group(0)}' matches no value of that unit")
                )
    return found


# --- anchors -----------------------------------------------------------------------

_ANCHORS: dict[str, str] = {
    "ascent": r"ascent|uphill|climb\w*|gain|aufstieg|bergauf|anstieg",
    "descent": r"descent|downhill|descend\w*|loss|abstieg|bergab",
    "horizontal": r"distance|flat|horizontal|strecke|ebene",
    "moving": r"moving|in motion|gehzeit|bewegung\w*",
    "total": r"total|overall|insgesamt|gesamt\w*",
    "estimated": r"estimate\w*|predicted|schätz\w*",
    "observed": r"recorded|observed|measured|actual|aufgezeichnet|gemessen",
}
_ANCHOR = re.compile(
    "|".join(f"(?P<{name}>\\b(?:{words}))" for name, words in _ANCHORS.items()),
    re.IGNORECASE,
)
# Which anchor families fit a fact; facts not listed are not checked.
_FAMILIES: tuple[tuple[str, frozenset[str]], ...] = (
    ("route.ascent", frozenset({"ascent"})),
    ("route.descent", frozenset({"descent"})),
    ("speed.ascent", frozenset({"ascent"})),
    ("speed.descent", frozenset({"descent"})),
    ("speed.horizontal", frozenset({"horizontal"})),
    ("estimate.baseline.horizontal", frozenset({"horizontal"})),
    ("estimate.baseline.vertical", frozenset({"ascent", "descent"})),
    ("estimate.baseline.moving", frozenset({"moving", "estimated"})),
    ("observed.*.moving", frozenset({"observed", "moving"})),
)
# Clauses: split at punctuation and at a new noun phrase after "and"/"while".
# German joins noun phrases without an article ("die Strecke 2:31 h und
# Aufstieg 2:59 h"), so every "und" ends a clause there.
_CLAUSE = re.compile(
    r"[,;:.!?]\s|\band (?:the|a|an|your)\b|\bwhile\b|\bwhereas\b|\bbut\b|"
    r"\bund\b|\bwährend\b|\baber\b|\bwohingegen\b",
    re.IGNORECASE,
)
_SPAN = re.compile(
    r"\b\d{1,2}:[0-5]\d\b(?:\s*h\b)?|(?<![\w.])x?\d[\d,]*(?:\.\d+)?"
    r"(?:\s*(?:km/h|m/h|km|m|%|deg|°|h|min)\b)?",
    re.IGNORECASE,
)


def _families(key: str) -> frozenset[str] | None:
    for pattern, fams in _FAMILIES:
        head, _, tail = pattern.partition("*")
        if key == pattern or (tail and key.startswith(head) and key.endswith(tail)):
            return fams
    return None


def _matches(span: str, fact: Fact) -> bool:
    """Whether a number span (e.g. "3:53 h", "1377 m", "x1.46") states the fact."""
    if fact.unit == "duration":
        m = re.match(r"(\d{1,2}):([0-5]\d)", span)
        if m is None:
            return False
        return abs(int(m[1]) * 60 + int(m[2]) - float(fact.value) / 60) <= 1
    num = re.search(r"\d[\d,]*(?:\.\d+)?", span)
    if num is None:
        return False
    return any(
        abs(r - value) <= tol + 1e-9
        for r in _readings(num.group(0))
        for value, tol in fact.numbers()
    )


def anchor_problems(
    statements: list[tuple[str, tuple[str, ...]]], facts: FactSet
) -> list[Finding]:
    found: list[Finding] = []
    for i, (text, keys) in enumerate(statements):
        anchored = [
            (facts[k], fams)
            for k in keys
            if k in facts and (fams := _families(k)) is not None
        ]
        if not anchored:
            continue
        start = 0
        for clause_end in [m.start() for m in _CLAUSE.finditer(text)] + [len(text)]:
            clause = text[start:clause_end]
            start = clause_end
            anchors = [
                (m.start(), m.end(), m.lastgroup)
                for m in _ANCHOR.finditer(clause)
                if m.lastgroup
            ]
            if not anchors:
                continue
            for num in _SPAN.finditer(clause):
                owners = [
                    (f, fams) for f, fams in anchored if _matches(num.group(0), f)
                ]
                if not owners:
                    continue

                def gap(a: tuple[int, int, str | None], n: re.Match[str] = num) -> int:
                    a_start, a_end, _ = a
                    return (
                        a_start - n.end() if a_start >= n.end() else n.start() - a_end
                    )

                nearest = min(gap(a) for a in anchors)
                near_families = {a[2] for a in anchors if gap(a) == nearest}
                if not any(near_families & fams for _, fams in owners):
                    label = owners[0][0].label
                    near = "/".join(sorted(f for f in near_families if f))
                    found.append(
                        Finding(
                            i,
                            f"'{num.group(0).strip()}' is the {label} but stands "
                            f"next to '{near}' words",
                        )
                    )
    return found


# --- provenance ------------------------------------------------------------------


def provenance_problems(
    statements: list[tuple[str, tuple[str, ...]]], facts: FactSet
) -> list[Finding]:
    """'Measured' / 'recorded' only with observed or calculated values."""
    found: list[Finding] = []
    for i, (text, keys) in enumerate(statements):
        word = _OBSERVATION.search(text)
        if word is None:
            continue
        cited = [facts[k] for k in keys if k in facts and facts[k].unit != "text"]
        if not cited:
            continue
        kinds = {f.provenance for f in cited}
        if kinds <= {"estimated", "fitted", "reference", "label"}:
            found.append(
                Finding(
                    i,
                    f"calls {'/'.join(sorted(kinds))} values '{word.group(0)}'",
                )
            )
    return found


# --- pairs (comparison) -----------------------------------------------------------

_LETTER = re.compile(r"\b([AB])\b|#(\d+)\b")
_UP = r"more|greater|higher|longer|bigger|larger|mehr|höher|länger|größer"
_DOWN = r"less|fewer|lower|shorter|smaller|weniger|niedriger|tiefer|kürzer|kleiner"
_DIRECTION_WORDS = {
    "up": re.compile(rf"\b(?:{_UP})\b", re.IGNORECASE),
    "down": re.compile(rf"\b(?:{_DOWN})\b", re.IGNORECASE),
}
_DIRECTION = {
    "more": "up",
    "longer": "up",
    "higher": "up",
    "less": "down",
    "shorter": "down",
    "lower": "down",
}


def _track_names(text: str, ids: dict[str, str]) -> list[tuple[int, int, str | None]]:
    return [
        (m.start(), m.end(), m[1].lower() if m[1] else ids.get(m[2]))
        for m in _LETTER.finditer(text)
    ]


def pair_problems(
    statements: list[tuple[str, tuple[str, ...]]], facts: FactSet
) -> list[Finding]:
    """Comparison facts ``a.*`` / ``b.*`` and ``diff.*`` with ``dir.*``.

    -   Numbers that state a cited ``a.*`` or ``b.*`` value (and not the
        other track's value) must all follow their track's name (letter or
        ID such as "#5": "B 554 m, A 1192 m"), or all precede it
        ("16 % for B and 26 % for A"). A swap breaks both patterns.
    -   A statement citing ``diff.X`` must not use a word of the opposite
        direction to ``dir.X``.
    """
    # A track may also be named by its ID ("#5"), as the draft introduces it.
    ids = {
        str(f.value).lstrip("#"): side
        for side in ("a", "b")
        if (f := facts.get(f"{side}.track")) is not None
    }
    found: list[Finding] = []
    for i, (text, keys) in enumerate(statements):
        cited = [facts[k] for k in keys if k in facts]
        numeric = [f for f in cited if f.unit != "text"]
        tracks = [f for f in numeric if f.key[:2] in ("a.", "b.")]
        others = [f for f in numeric if f.key[:2] not in ("a.", "b.")]
        names = _track_names(text, ids)
        values: list[tuple[str, str, str | None, str | None]] = []
        for num in _SPAN.finditer(text):
            owners = {f.key[0] for f in tracks if _matches(num.group(0), f)}
            if len(owners) != 1 or any(_matches(num.group(0), f) for f in others):
                continue  # not a track value, or ambiguous (e.g. diff = A's value)
            before = [n for s0, e0, n in names if e0 <= num.start()]
            after = [n for s0, e0, n in names if s0 >= num.end()]
            values.append(
                (
                    num.group(0).strip(),
                    owners.pop(),
                    before[-1] if before else None,
                    after[0] if after else None,
                )
            )
        # Either every value follows its track's name ("B 554 m, A 1192 m")
        # or every value precedes it ("16 % for B and 26 % for A").
        if values and not (
            all(b == want for _, want, b, _ in values)
            or all(a == want for _, want, _, a in values)
        ):
            for number, want, b, _ in values:
                if b != want:
                    found.append(
                        Finding(
                            i,
                            f"'{number}' is the value of track {want.upper()} "
                            "but is not written next to it",
                        )
                    )
        for key in keys:
            if not key.startswith("diff."):
                continue
            direction = facts.get("dir." + key[5:])
            if direction is None:
                continue
            side = _DIRECTION.get(str(direction.value))
            if side is None:
                continue
            opposite = _DIRECTION_WORDS["down" if side == "up" else "up"]
            if (m := opposite.search(text)) is not None:
                found.append(
                    Finding(
                        i,
                        f"'{m.group(0)}' reverses the difference: B has "
                        f"{direction.value}, not the opposite",
                    )
                )
    return found
