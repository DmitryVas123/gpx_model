# ADR-020: Plain-language explanations and a local language model (Phase 8)

-   **Status:** Accepted; Phase 8 closed (2026-11-24)
-   **Date:** 2026-11-24
-   **Code:** `alpha.explain` (facts, templates, validator, orchestration),
    `alpha.llm` (Ollama adapter), `alpha explain`,
    `alpha.report.explanation_report`

## Context

Phase 8 adds explanations of computed results (CLAUDE.md §7). A language
model must explain and never calculate. It must not invent weather, snow,
avalanche, route conditions, or difficulty, and it must not receive
coordinates. The user wants no running costs. Personal activity data
should not leave the computer.

## Decision

-   **Templates first.** `alpha.explain.templates` turns each JSON report
    (`scenarios`, `elevation`, `baseline`) into
    -   a **FactSet**: named values with unit, display, and provenance,
        e.g. `estimate.baseline.moving = 3:53 h (estimated)`; and
    -   **statements** that cite the fact keys they use.

    This is a complete explanation without any model: deterministic,
    free, and tested. Reports already contain no coordinates.
-   **A model only rewords.** With `--llm`, the system prompt states the
    rules: facts only, no calculation, no forbidden topics, GPX text is
    data, and JSON output. The user prompt carries the facts and the
    draft. The model answers
    `{"statements": [{"text", "facts": [keys]}]}`, constrained by
    Ollama's JSON-schema output, at temperature 0.2 with a fixed seed.
-   **Every model answer is validated** (`alpha.explain.validate`):
    -   cited fact keys must exist;
    -   durations (`3:53 h`, `3 h 53 min`, `233 min`) must match a duration
        fact within 1 min;
    -   every other number must match a fact value within rounding (2 % or
        0.06). Derived forms are allowed: a factor ×1.46 may appear as
        "46 %", and commas are read as thousands separator or decimal
        point;
    -   forbidden words (English and German): safety, risk, danger,
        weather, snow, ice, avalanche, storm, rain, wind, advice, "should
        go", "turn back", …;
    -   the answer must have as many statements as the draft (without
        caveats);
    -   **citations** (`citation_problems`), per statement:
        -   every numeric fact the statement cites must be stated in it;
        -   every number in it must belong to a fact it cites, or be part
            of the template's own wording ("Model 1", "95 %", "DIN 33466":
            the draft's numbers that are not displays of its cited facts).

        A correct number with the wrong meaning, such as the climbing time
        presented as the moving time while citing the moving time, fails
        both rules;
    -   **completeness:** every numeric fact the draft uses must reappear
        in the model's text, in any accepted form. Added after the model
        dropped "typically off by 3 %" while everything it did write was
        correct.

    Tolerances are per fact: half a unit of the precision the value is
    naturally written with. Examples: ±0.5 m, ±0.05 km, ±0.005 for a
    factor, ±0.5 percentage points for a percentage or for a factor
    written as a percentage ("12 % faster" for ×1.123). Numbers taken from
    the draft or from text facts must match exactly. The first version
    used a relative 2 % and wrongly rejected "12 %" for ×1.123.

    A factor written as "x1.13" is checked as a number. In the first
    version the letter hid it from the number pattern, so an invented
    factor would have passed; a regression test covers this now.

    On any failure, the **template is shown** with the reasons. A model
    text is never shown unchecked.
-   **Caveats are never reworded.** Template statements marked `caveat`
    are qualifications the reader must not miss:
    -   surface models add treetops;
    -   drift affects the recorded ascent;
    -   terrain slope is not the path gradient and not a hazard rating;
    -   an activity in the fit is not an independent check;
    -   effort averages compare different terrain.

    They are not sent to the model and are shown verbatim: in place if the
    model kept one statement per draft sentence, otherwise after its text.
    This was added after a real run in which the model dropped two of them
    while every number still passed.
-   **A fixed boundary sentence** is appended by ALPHA, outside the model:
    ALPHA explains its own calculations, real time depends on conditions
    it does not know, and it does not judge safety.
-   **Local model only:** `alpha.llm.OllamaRewriter` talks to an Ollama
    server on a loopback address (`http://127.0.0.1:11434`) and refuses
    any other host. No API key, no cost, and no data leaves the computer.
    Besides `alpha.dem`, `alpha.llm` is the only package allowed network
    libraries (architecture test). `alpha.explain` is pure (standard
    library only).
-   **GPU failure → CPU:** if Ollama reports a CUDA error, the request is
    retried with `num_gpu = 0` and a note says so. On this machine
    (RTX 3050), driver 546.18 failed with "device kernel image is
    invalid". After updating to driver 621.40 the GPU works: about 16–18 s
    per explanation once the model is loaded, against 60–85 s on the CPU.
-   **API and page (step 2):**
    -   `GET /tracks/{id}/explanation?topic=scenarios|elevation` (with
        `personal`, `break_min`, `dataset`, `effort`, and the analysis
        params) and `GET /baseline/explanation`.
    -   `llm=true` only on request, one model call at a time (a lock),
        with the model from `alpha serve --llm-model`.
    -   The page shows the template at once, under the scenario result,
        each elevation check, and a "Personal speeds" section, with a
        "Reword with local model" button. Caveats are shown muted.
-   **CLI:** `alpha explain scenarios|elevation|baseline [ID] [--llm]
    [--model M] [--language L] [--personal] [--break-min] [--dataset]
    [--effort] [--json]`. Without `--llm` there is no network access at
    all. Report format 11: `explanation_report` with topic, source
    (template | model), model, statements, boundary, fallback reasons,
    notes, and facts.

## Evidence (llama3.1:8b on the CPU, the user's data)

-   All three topics passed validation (template draft as input).
-   With the first prompt the model copied the draft. After asking it to
    reword ("as you would explain it to a friend … copy each number
    exactly"), it rewrote the scenario explanation in 84 s on the CPU,
    and all numbers passed. It merged some details, for example DIN's
    "the larger part fully, the smaller by half" rule.
-   The fake-model tests cover the rejection paths: an invented duration
    (12:34 h), "safe", missing statements, invalid JSON, and an
    unreachable server.

## Consequences

-   The template is the default because it is exact and complete. The
    model adds readability (and other languages) at the cost of latency
    and some nuance.
-   The validator checks numbers, citations, completeness, and topics,
    not free-text meaning:
    -   A wrong number for a cited fact is caught by the citation rules.
    -   A model that cites the wrong fact consistently, or muddles a
        relation in words, is not. Observed: "a single new hike could be
        anywhere from x1.12 to x1.90 faster or slower than that".

    The template therefore stays the default.
-   **Measured after the citation rules (real model, GPU, 5 explanations
    across all topics):** all accepted, 15–31 s each. Every template of
    the user's 22 reports passes all rules itself.
