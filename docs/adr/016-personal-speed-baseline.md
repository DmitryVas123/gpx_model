# ADR-016: Personal speed baseline (Phase 7, step 1)

-   **Status:** Accepted
-   **Date:** 2026-11-24
-   **Code:** `alpha.modelling.baseline`, `alpha.application.baseline`,
    `alpha baseline`, `alpha.report.baseline_report`

## Context

Phase 7 fits personal assumptions from recorded activities (ADR-005).
The first and most transparent model is a single **speed multiplier** on
DIN 33466. On the four reference activities, per-activity multipliers
range from ×1.48 to ×2.38 (DGM1 elevation), and the spread between them
is about 21 % (ln-scale standard deviation 0.20).

## Decision

-   **Model:** `moving time = DIN moving time(reference speeds) / k`, with
    DIN 33466 standard as the reference. For each activity,
    `k_i = DIN moving / observed moving` is exact; it is the implied
    multiplier of ADR-005.
-   **Fit:**
    -   **In log space:** multiplicative errors, so 20 % faster and 20 %
        slower are symmetric.
    -   **Per route group first:** activities linked to the same plan form
        one group, and every other activity is its own group. Repeats of a
        route are not independent and count once. Then
        `k = exp(mean of group means)`.
    -   **Two 95 % intervals,** Student t with `groups − 1` degrees of
        freedom:
        -   for the **average** k: `± t·s/√n`;
        -   for **one new activity:** `± t·s·√(1 + 1/n)`, which is much
            wider. More data narrows the first, not the second.
    -   **Validation by leaving out one route group at a time:** predict
        its activities from the others, report the median and maximum
        `|predicted / observed − 1|`.
    -   **Result:** `DinAssumptions(source=fitted)` with the reference
        speeds × k and a label stating the activity and route counts.
-   **Gate:** nothing is fitted below `min_groups` = **5 distinct routes**.
    The status is "not enough data". The per-activity ratios are still
    shown, because they are observations.
-   **Eligibility** (`BaselineParams`): kind `activity`; GPX type
    `hiking` or `walking`; moving time ≥ 30 min; ascent known; the chosen
    elevation series stored. With `--elevation dem:BY_DGM1` an activity
    without that profile is **excluded with the reason**, never silently
    switched to recorded elevation. Every exclusion is listed.
-   **Package boundaries:**
    -   `alpha.modelling` is pure: it imports only the standard library,
        numpy, and `alpha.domain` (enforced by `tests/test_architecture.py`),
        and the domain never imports it.
    -   The application layer collects samples from storage and calls it.
        This refines ADR-005's "reads stored analyses through the
        application layer": modelling receives the samples instead of
        reading them, which keeps it testable with synthetic data.
-   **Not persisted:** the fit is recomputed on demand in milliseconds,
    like scenarios.
-   **Output:** `alpha baseline [--elevation …] [--min-routes N] [--json]`,
    report format 7. API and page come in a later step.

## Why 5 routes

With n groups, the 95 % half-width of the average in log space is
`t(n−1)·s/√n`. At s ≈ 0.20:

| Routes | Half-width | Average k known to about |
|---|---|---|
| 3 | 4.30 · 0.20 / 1.73 = 0.50 | ±50–65 % |
| 5 | 2.78 · 0.20 / 2.24 = 0.25 | ±25–28 % |
| 10 | 2.26 · 0.20 / 3.16 = 0.14 | ±13–15 % |
| 20 | 2.09 · 0.20 / 4.47 = 0.09 | ±9 % |

Below 5 the average is not meaningfully narrower than the spread between
single hikes, so a "personal" value would mislead. The gate is a
parameter (`--min-routes`) and is reported in every output.

## Evidence (four activities, DGM1 elevation, gate lowered to 2 for inspection)

| | Value |
|---|---|
| k | ×1.82 |
| Average, 95 % | ×1.32 – ×2.50 |
| One new hike, 95 % | ×0.89 – ×3.71 |
| Leave-one-route-out error | median 17 %, max 44 % (Activity A) |

The largest error is the steepest and hardest effort. This suggests that
one multiplier for flat and climbing speed together does not fit. The
next step tests exactly that.

## Consequences

-   **Next steps:**
    -   step 2 (done, ADR-017): flat and vertical factors. On the
        reference data it cuts the leave-one-route-out error from 17 % to
        7 % (median);
    -   step 3: mark hard efforts;
    -   step 4: use fitted assumptions in `alpha scenarios`, with the
        intervals shown.
-   The threshold of 5 should be revisited once the observed spread is
    known from more activities. A smaller spread would justify reporting
    earlier.
