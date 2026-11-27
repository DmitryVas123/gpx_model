# ADR-017: Separate flat and vertical speed factors (Phase 7, step 2)

-   **Status:** Accepted
-   **Date:** 2026-11-24
-   **Code:** `alpha.modelling.split`, `alpha.application.baseline`,
    `alpha baseline` (model 2)

## Context

One speed multiplier (ADR-016) predicts the reference activities with a
leave-one-route-out error of median 17 % and maximum 44 %. The worst case
is the steepest route (Activity A, ×2.38 against ×1.48–1.83 for the
others). This suggests that this user's climbing speed differs from their
flat speed by more than DIN's standard ratio assumes.

## Decision

-   **Model 2:** DIN 33466 with the horizontal speed × `kh` and **both**
    vertical rates × `kv`:
    -   `h = distance / (H·kh)`;
    -   `v = (ascent / U + descent / D) / kv`;
    -   `moving = max(h, v) + min(h, v) / 2`.

    The scenario method is unchanged. The result is
    `DinAssumptions(source=fitted)` with the speeds `H·kh`, `U·kv`, and
    `D·kv`.
-   **Ascent and descent share one factor.** On loops ascent ≈ descent,
    so moving time alone cannot separate them; the reference activities
    include only one one-way climb. DIN's U : D ratio (300 : 500) is kept.
    Separate factors need several one-way routes (a later step).
-   **Fit:**
    -   Least squares of `ln(predicted / observed)`; every route group has
        total weight 1.
    -   Deterministic two-stage grid search over `ln kh` and `ln kv`:
        factors 0.2 to 8, step 0.02, then 0.001 around the best point. The
        max/min formula has kinks, which a grid handles without starting
        values or local-minimum surprises. No SciPy is needed.
    -   An optimum on the grid edge returns **not identifiable**.
-   **Identifiability gate:**
    -   at least `split_min_groups` = **8 distinct routes** (two
        parameters, and the jackknife needs several groups); and
    -   the routes must differ in mix. The **ratio range** is max/min
        over route groups of the reference `v / h`, and it must be at
        least **1.5**. If all routes have the same mix, every combination
        of `kh` and `kv` along a curve fits equally well. The status is
        then *not identifiable*, with the reason.
-   **Uncertainty:** the leave-one-route-out refits give
    -   the validation error (as in model 1), and
    -   jackknife standard errors of `ln kh` and `ln kv`:
        `SE = √((n−1)/n · Σ(θ₋g − θ̄)²)`, with intervals `exp(θ ± t·SE)`.
-   **Comparison:** `alpha baseline` shows both models on the same
    activities, with the leave-one-route-out error per activity (LOO 1 and
    LOO 2) and the median of each. The model that predicts better is named.
    The CLI does not pick one automatically; step 4 decides how
    `alpha scenarios` uses them.
-   Report format 8 groups the two models under `models.multiplier` and
    `models.split`.

## Evidence (four activities, DGM1 elevation, gates lowered for inspection)

Route mix: `v / h` from 1.00 (Activity D) to 2.34 (Activity A), a range of
×2.35, which passes the 1.5 requirement.

| | Model 1: one multiplier | Model 2: flat / vertical |
|---|---|---|
| Factors | ×1.82 | flat ×1.20, vertical ×3.37 |
| Speeds | 7.3 km/h, 545 / 908 m/h | 4.8 km/h, 1011 / 1684 m/h |
| LOO error, median / max | 17 % / 44 % | **7 % / 10 %** |
| 95 % interval | average ×1.32–2.50 | flat 0.84–1.70, vertical **0.36–31** |

-   Model 2 explains Activity A without treating it as an outlier.
    This user walks flat terrain at about the standard pace and climbs
    much faster than the standard ratio implies.
-   The vertical interval is huge because, with four routes, the vertical
    factor rests mostly on the one steep route; leaving it out moves the
    estimate far. This is what the 8-route gate is for.
-   Part of Activity A's speed may be effort ("push"), not terrain. The
    user marked it and Activity B as hard (ADR-018). Effort and steepness
    are therefore confounded in the reference data, and the vertical
    factor above should not be read as climbing ability.

## Consequences

-   **New activities that help most:** steep routes (`v / h` > 1.5) and
    flat ones (`v / h` < 1). They spread the mix and pin both factors.
-   A gradient-band pace model (ADR-005: a new registered method) remains
    the next level if the split still leaves systematic errors.
