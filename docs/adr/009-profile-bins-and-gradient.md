# ADR-009: Profile bins, gradient, and outliers

-   **Status:** Accepted (bin length provisional)
-   **Date:** 2026-11-22
-   **Code:** `alpha.domain.profile`, `alpha.domain.climbs`

## Context

Point-to-point gradient is dominated by noise. On `input.gpx`, 1 m
elevation rounding over sub-metre steps produced 45 steps above 45 % and
a maximum of 414 %. The exploration script clipped gradient to ±45 %,
which hid both the artifacts and the genuinely steep upper section of the
route.

## Decision

-   Resample along **horizontal cumulative distance**, which does not
    grow across source-segment boundaries. Bins are fixed lengths of
    `profile_bin_m` (50 m) from the start; the last bin is shorter.
-   Elevation and time are linearly interpolated **per source segment**.
    A bin's `ele_change_m` and `elapsed_s` sum only within-segment
    changes, so a jump between segments is never a gradient or time.
    Zero-length steps keep their first point, so the interpolation axis
    is strictly increasing.
-   `gradient_percent = ele_change_m / bin length × 100`. It is `None`
    when elevation is incomplete, or for a final bin shorter than half
    the nominal length.
-   **No clipping.** Bins steeper than `gradient_outlier_percent` (100 %)
    raise `GRADIENT_OUTLIER` with their bin indices; values are kept.
-   Gradient bands for distributions are fixed and half-open
    (`[low, high)`), plus `unknown`:
    `< -25 | -25..-15 | -15..-5 | -5..5 | 5..15 | 15..25 | 25..40 | >= 40`.
-   Climb sections: bins at ≥ 8 % in one direction, bridging milder bins
    up to 150 m in a row. A bin steep in the opposite direction, or
    without gradient, always ends a section, so up and down sections
    never overlap. Net change is measured in the section's direction and
    must reach 50 m.

## Evidence

Steepest bin and count of bins ≥ 40 % on `input.gpx`:

| Bin length | Max gradient | Bins ≥ 40 % |
|---|---|---|
| step (no binning) | 414 % | 45 steps > 45 % |
| 25 m | 52 % | 15 |
| **50 m** | **49 %** | **6** |
| 100 m | 44 % | 2 |

The recorded activities (1 Hz) have no bins above 40 % at 50 m. The
density test (the same synthetic hill sampled at 1 m and 15 m) gives bin
gradients within 0.3 percentage points.

## Consequences

-   Gradients are comparable across files with very different point
    spacing.
-   Short steep steps shorter than a bin (a 10 m scramble) are averaged
    away. The profile describes sustained steepness, not technical
    difficulty, which ALPHA does not infer anyway.
-   Track-derived gradient is not terrain slope (DEM work is Phase 6).
