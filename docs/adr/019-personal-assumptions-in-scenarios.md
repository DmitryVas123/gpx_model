# ADR-019: Personal (fitted) speeds in duration scenarios (Phase 7, step 4)

-   **Status:** Accepted
-   **Date:** 2026-11-24
-   **Code:** `alpha.application.baseline.personal_assumptions`,
    `alpha.application.scenarios.estimate_personal`,
    `alpha scenarios --personal`, `GET /tracks/{id}/scenarios?assumptions=personal`,
    `GET /baseline`, page selector

## Context

Steps 1–3 (ADR-016 to ADR-018) fit personal speeds with intervals and
validation. Planning needs them where durations are estimated, without
weakening the rules: fitted values must say what they rest on, and
missing data must never be hidden by a preset.

## Decision

-   **Opt-in:** `alpha scenarios ID --personal [--effort …]`, the API's
    `assumptions=personal`, or the page's "Personal" option. The default
    stays the DIN 33466 standard or the user's typed speeds. Typed speeds
    and `--personal` are mutually exclusive (exit 3 / 422).
-   **Same analysis params for fit and estimate:** the baseline is fitted
    with the elevation series and parameters of the requested estimate.
    Speeds fitted on one series are never applied to ascents of another.
-   **Model choice:** among the fitted models, the one with the smaller
    leave-one-route-out median error; model 1 on a tie, being simpler.
    The choice, sample counts, effort selection, and error are shown in
    the CLI, the JSON (`personal`), and the page.
-   **No fallback:** if no model is fitted, the request fails with
    `BaselineNotFittedError` (CLI exit 5; API 409 `BASELINE_NOT_FITTED`),
    and the message names the missing routes.
-   **Range from the user's own variation:** by default the
    faster/slower scenarios are the 95 % range for a single new hike:
    -   model 1: `single_interval / k`;
    -   model 2: `exp(±t(n−2)·residual_spread·√(1+1/n))`.

    Custom `--speed-multipliers` replace it, and then the note says
    "sensitivity to pace only". Notes never claim both meanings.
-   **Honest self-checks:** estimating an activity that is part of the fit
    adds the note that its observed time is not an independent check.
-   **Breaks and delay** remain the user's inputs and are added unchanged.
-   Report format 10: scenario reports carry `personal` (null for preset
    or user assumptions). `GET /baseline` serves the baseline report,
    validated by a strict schema.

## Evidence (seven activities, 2026-11-24)

The three new activities were
-   in Oytal (Bavaria),
-   near Riezlern (Austria, no DGM1), and
-   at Chiusi (Italy, flat, no DGM1).

All five normal-effort routes have recorded elevation, so the fit uses
it.

| Elevation | Model 1 k | One new hike | LOO median / max |
|---|---|---|---|
| recorded | ×1.46 (5.8 km/h, 438 / 730 m/h) | ×1.12–1.90 | 3 % / 16 % |
| COP30 | ×1.59 | ×1.16–2.19 | 7 % / 20 % |

Model 2 is still gated (5 of 8 routes). The route mix is now wide: `v / h`
runs from 0.02 (Chiusi) to 1.19, a range of ×50. An inspection-only
preview with the gate at 5 gives:

-   flat ×1.30 (1.08–1.57) and vertical ×2.12 (1.18–3.80);
-   LOO median 4 %, max 9 %;
-   hard efforts against model 2: Gleitweg +9 %, Nebelhorn +20 %. Against
    model 1 they are +21 % and +56 %, so most of Nebelhorn's speed was
    terrain, not effort.

Nebelhorn_push estimated with the personal speeds (hard effort, not in
the fit): baseline 3:53 h moving, range 2:59–5:03 h; observed 2:29 h,
×1.56 faster than the normal-effort fit.

## Consequences

-   Recorded elevation is the consistent choice for this user's fit until
    every route has a terrain model. COP30 inflates forest ascents
    (ADR-013), and DGM1 covers only Bavaria. Planned routes from mapy.com
    carry the planner's own terrain elevation, which is close to a DEM, so
    a small mismatch with barometric training data remains. It is
    documented, not corrected.
-   With 3 more normal-effort routes, model 2 passes its gate and will be
    chosen automatically if it validates better.
