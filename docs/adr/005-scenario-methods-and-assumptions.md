# ADR-005: Scenario methods, assumption sources, and where ML lives

-   **Status:** Accepted
-   **Date:** 2026-11-22
-   **Code:** `alpha.domain.scenarios`, `alpha.application.scenarios`

## Context

Phase 4 adds duration estimates (Journey B). Later phases may add a
personal performance model (Phase 7). Two concerns are easily mixed up:

-   **the formula** that turns route metrics into a duration, and
-   **where the formula's numbers come from**: typed in, a published
    preset, or fitted from the user's history.

A personal model is usually not a different formula. "My DIN speeds" are
still DIN 33466, with fitted instead of default speeds.

## Decision

``` text
Analysis ──► Assumptions ──────────────► Method (registry) ──► ScenarioResult
             source = user | preset        din33466 (now)        method, assumptions
                    | fitted (Phase 7)      later: pace per        + source, inputs,
                                            gradient band          breakdown, notes
```

-   **Methods** are pure formulas in a registry (`SCENARIO_METHODS`,
    name → spec with version and description). The first is `din33466`:
    -   `h = distance / horizontal speed`;
    -   `v = ascent / ascent rate + descent / descent rate`;
    -   `moving = max(h, v) + min(h, v) / 2`;
    -   `total = moving + breaks + delay`.

    Distance is the jitter-filtered 2D `distance_m`; ascent and descent
    are the analysis values. Internal units: m/h for speeds, seconds for
    times.
-   **Assumptions** carry their source (`user`, `preset`, `fitted`) and an
    optional label. The only preset is "DIN 33466 standard" (4 km/h,
    300 m/h up, 500 m/h down): published reference values for an average
    hiker, labelled as such and never presented as personal. If the user
    overrides any speed, the assumptions become `user`. Breaks and delay
    are always the user's (default 0) and are shown explicitly.
-   **Range:** speed multipliers (default 1.2 / 1.0 / 0.8, user-settable)
    scale all speeds. Labels are faster / baseline / slower. The range
    reflects pace sensitivity only, and the notes say so.
-   **Every result** carries method and version, assumptions with
    source, the calculated inputs, a breakdown per scenario, the analysis
    version and params hash, and uncertainty notes. One note is
    data-driven: the distance steeper than 25 %, which the method does
    not model beyond total ascent and descent.
-   **Observed next to estimated:** for an activity, its own recorded
    times; for a planned route, those of every linked activity. Each gets
    an **implied speed multiplier**, computed on that activity's own
    distance and ascent (it may have taken another path). This is the
    multiplier at which DIN's moving time equals the recorded one. It is
    a ratio from a single observation, explicitly not a personal model.
-   **ML and fitting live outside the domain.** A future
    `alpha.modelling` package (Phase 7) reads stored analyses through the
    application layer, fits parameters (first a transparent least-squares
    fit of DIN speeds, later possibly scikit-learn), and returns
    `DinAssumptions(source=fitted, ...)` with sample count and error range.
    The domain defines the contract and labels; it never imports a
    modelling library or trains anything. A genuinely different model
    (e.g. pace per gradient band) becomes a new registered method whose
    parameters are, again, supplied from outside.
-   **Not persisted yet.** Scenarios are deterministic given analysis,
    method, and assumptions, and recomputing is instant. A `scenarios`
    table (DATA_MODEL.md §10) waits until estimates must be kept, for
    example "what did I estimate before the hike?".

## Evidence

On the reference data with DIN 33466 standard values:

| Track | Inputs | DIN baseline moving | Observed moving | Implied multiplier |
|---|---|---|---|---|
| Plan 1 (plan) | 6.81 km, +1368 / −0 m | 5:25 h | — | — |
| Activity A (linked) | 8.33 km, +1377 / −18 m | ~5:40 h | 2:29 h | ×2.28 |

The standard values are far from this user's pace. That is exactly why
assumptions must be visible and why fitted assumptions (Phase 7) need a
sample count and error before they are shown as personal.

## Consequences

-   Adding a method is one registry entry plus its assumptions type; the
    CLI and report already show method name and version.
-   The registry is currently typed for `DinAssumptions`; it generalises
    when a second method arrives.
-   The implied multiplier gives early, honest feedback on the
    assumptions without claiming a personal model.
