# ADR-004: Ascent and descent with a hysteresis threshold

-   **Status:** Accepted (default value provisional)
-   **Date:** 2026-11-22
-   **Code:** `alpha.domain.elevation`

## Context

Summing every positive elevation difference overstates ascent on noisy
data, and the overstatement grows with sampling rate. The exploration
script used a 5-point rolling mean, whose strength depends on point
spacing (0.6 m to 15 m in the reference data, ARCHITECTURE.md §4.3).

Options considered:

1.  **Rolling mean over N points:** spacing-dependent; rejected.
2.  **Rolling mean over a distance or time window:** spacing-independent,
    but flattens real short climbs and needs resampling first.
3.  **Hysteresis (dead band):** count a climb only once elevation has
    reversed by at least a threshold. Independent of spacing, no
    resampling, single parameter in metres, easy to explain.

## Decision

Option 3, on raw elevation, per source segment:

-   A turning point is confirmed when elevation reverses by at least
    `ascent_threshold_m` from the running extreme.
-   Confirmed climbs are counted **in full**, peak to trough, not reduced
    by the threshold. The final, unconfirmed leg is counted too.
-   Threshold 0 gives the plain sum of differences.
-   Elevation metrics are `None` unless every point has elevation.
    Partial sums would silently understate ascent. Interpolating missing
    points is a possible later option, which must be shown as estimated.
-   Default `ascent_threshold_m = 3.0` for every elevation source.

Guaranteed properties (tested): `|(ascent − descent) − (end − start)| <
2 × threshold` per segment; ascent never increases with a higher
threshold; a reversed track swaps ascent and descent within
2 × threshold.

## Evidence

Ascent / descent (m) by threshold on the reference files:

| File | 0 m | 1 m | 3 m | 5 m | 10 m |
|---|---|---|---|---|---|
| input.gpx (planner) | 1368 / 0 | 1368 / 0 | 1368 / 0 | 1368 / 0 | 1368 / 0 |
| input2.gpx (planner) | 1301 / 10 | 1301 / 10 | 1301 / 10 | 1301 / 10 | 1301 / 10 |
| Nebelhorn_push (Strava) | 1384 / 26 | 1384 / 25 | 1377 / 18 | 1377 / 14 | 1372 / 14 |
| Gleitweg… (Strava) | 1200 / 1208 | 1197 / 1205 | 1192 / 1200 | 1178 / 1189 | 1178 / 1189 |

Planner elevation is already smooth, so it is unaffected. Strava
elevation (0.1 m resolution, likely barometric) loses about 0.5 % at
3 m. The 3 m default mainly removes sub-metre jitter, like the
18 m of "descent" on a one-way ascent.

## Consequences

-   Results are comparable across planned and recorded tracks.
-   Raw GPS (non-barometric) elevation is much noisier; 3 m may be too
    low for it. Revisit when such a file is available; a per-source
    default would then become part of `AnalysisParams`.
## Comparison with Strava (2026-11-22)

| Activity | Strava gain | Unfiltered sum of GPX `<ele>` (threshold 0) | ALPHA (3 m) |
|---|---|---|---|
| Nebelhorn_push | 1443 m | 1384 m | 1377 m |
| Gleitweg… | 1309 m | 1200 m | 1192 m |

Strava's gain is **higher than the plain sum of every positive step** in
the exported elevation. No threshold or smoothing can produce more gain
than that sum from the same series, so Strava computes gain from
different elevation data, most likely its own terrain-model correction
rather than the exported values. The comparison therefore cannot
calibrate the threshold. It shows only that the 3 m threshold removes
about 0.5 % relative to the exported data.

Revisit when an independent reference exists for the same elevation
series, or when DEM enrichment (Phase 6) provides a second source.
