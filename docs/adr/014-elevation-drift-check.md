# ADR-014: Drift of recorded elevation against a reference model

-   **Status:** Accepted (Phase 6)
-   **Date:** 2026-11-22
-   **Code:** `alpha.domain.reference_elevation` (`DriftParams`,
    `elevation_drift`, `drift_warning`), `WarningCode.ELEVATION_DRIFT`

## Context

The elevation check (ADR-012) reports the median, spread, and largest
difference between recorded elevation and a DEM. On Nebelhorn_push the
median is ≈ 0, yet near the summit the recorded series is about 40 m below
both COP30 and DGM1 (ADR-013). The barometer drifted during the ascent.
Neither the median nor the largest difference shows that the disagreement
**grows along the track**. The largest difference cannot tell drift from a
short spike, such as the 25 m excursion on Gleitweg at km 6.85.

## Decision

-   **Measure:** `d = recorded − reference` where both exist, on the
    filtered distance axis (ADR-010).
    1.  Median of `d` per `bin_m` = 100 m, which removes point noise.
    2.  Rolling median over `window_m` = 500 m of bins (±2 bins), which
        removes excursions shorter than about half the window.
    3.  From the smoothed series, report the level at the start and the
        end, the minimum and maximum with their positions, and the
        **range** (max − min).
    Bins without overlapping data are skipped. Fewer than two bins with
    data give no drift (`None`).
-   **Warn:** `ELEVATION_DRIFT` when the range is ≥ `warn_range_m` =
    20 m. The message names the start level and the extreme farther from
    it, with its position. A constant offset is not drift, because the
    range ignores the level.
-   **Where it appears:** the elevation check only (`alpha enrich`,
    `GET/POST /tracks/{id}/dem`, the page), in `comparison.drift` and
    `warnings`. With a **surface** model, the CLI and page add that forest
    can cause part of the drift and that a terrain model is the better
    reference.
-   **Parameters:** `DriftParams` lives next to the comparison, not in
    `AnalysisParams`. It does not change any analysis result, so it does
    not enter the params hash or `ALGORITHM_VERSION`. Report format 5 adds
    the fields.

## Calibration (reference activities, 500 m window)

| Track | Range vs DGM1 | Range vs COP30 | Start → end (DGM1) | Warning |
|---|---|---|---|---|
| Nebelhorn_push | 47 m | 36 m | +2 → −36 m (mostly the last km) | yes |
| Gleitweg… | 17 m | 13 m | +3 → +1 m | no |
| Lunch Hike | 15 m | 21 m | +3 → +3 m | DGM1: no, COP30: yes |
| Wanderung am Abend | 13 m | 22 m | +3 → +2 m | DGM1: no, COP30: yes |

**Against the terrain model the threshold separates cleanly:** 13–17 m
for the three activities without drift, 47 m for the drifting one.
**Against COP30 (a surface model) forest produces false warnings:** the
two new activities reach 21–22 m only against COP30. The CLI and page
already add the forest caveat to warnings against a surface model. Prefer
the terrain model's elevation check when both are stored.

Window sensitivity, range vs DGM1:

| Track | 300 m | 500 m | 1000 m |
|---|---|---|---|
| Nebelhorn_push | 49 m | 47 m | 33 m |
| Gleitweg… | 24 m | 17 m | 12 m |

500 m is the shortest window that keeps Gleitweg's local excursions below
20 m. It is still short enough to catch a drift that builds up over the
last kilometre.

## Consequences

-   Four activities calibrate the threshold (2026-11-22). Revisit it as
    more activities are imported. The regression test
    (`tests/regression/test_dem_reference.py`) pins the outcome for both.
-   The check does not say *why* the series drifts (weather, barometer
    calibration, a cable-car ride). It reports the size and position.
-   It does not yet appear in `show` with recorded elevation, because
    analysis warnings are part of the cached, hashed analysis. If that is
    wanted, add it as a separate note derived from the stored profile.
