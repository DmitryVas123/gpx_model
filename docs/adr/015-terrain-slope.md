# ADR-015: Terrain slope from the elevation model

-   **Status:** Accepted (completes Phase 6)
-   **Date:** 2026-11-22
-   **Code:** `alpha.domain.terrain`, `alpha.application.elevation`,
    `alpha.dem.tiles.assign_tiles`, table `terrain_slopes`

## Context

The track gradient (DATA_MODEL.md §5.4) is the rise over the distance
actually walked. It says nothing about the ground beside the path: a level
traverse across a steep hillside has a gradient of 0 %. The DEM profiles
(ADR-012, ADR-013) make a second, independent measure possible: the
steepness of the terrain at each position.

## Decision

-   **Definition:** terrain slope = `atan(|∇z|)` in degrees, from central
    differences of the model at four probes `baseline_m` = 15 m north,
    south, east, and west of each sample point. The probes are geodesic
    offsets (pyproj). The slope is independent of the direction of travel.
-   **Baseline 15 m (30 m span):**
    -   one COP30 pixel;
    -   for the 1 m DGM1, wide enough to describe the hillside rather than
        the path cut, a rock, or a ditch.
    -   The same baseline for every dataset keeps them comparable.
-   **Sampling:** at the same knots as the elevation profile (every 20 m,
    ADR-012). Values between knots are interpolated along the track. Any
    probe without data gives NaN; nothing is invented.
-   **Storage:** table `terrain_slopes` (schema 3, additive upgrade). It
    holds one float64 per point plus baseline and spacing, per track and
    dataset.
    -   New profiles store the slope in the same pass over the tiles.
    -   An existing profile without slope gets it on the next
        `alpha enrich`, from cached or local tiles, without re-sampling the
        elevation.
    -   If the tiles are unavailable, the elevation check still works and
        says why the slope is missing.
-   **Output:** part of the elevation check (`terrain_slope` in report
    format 6; CLI `alpha enrich`; API; page).
    -   Distance per 10° band: `<10`, `10-20`, … `>=50`. Each step of the
        filtered distance counts with the slope at its end.
    -   Distance-weighted median and 90th percentile, and the steepest
        value with its position.
    -   Labelled *calculated*, with the explicit meaning: "steepness of
        the ground around the track, not the track gradient and not a
        hazard rating".
-   **Not in `analyse()`:** the analysis stays a function of the track
    and `AnalysisParams`. Terrain slope depends on an external model, so
    it lives with the model's profile.
-   **Product boundary:** degrees are reported as measurements only.
    There is no classification such as "avalanche terrain" or "exposed",
    and no statement about safety (CLAUDE.md §1).

## Tile margins (COP30)

The probes made a latent issue visible. A track near a 0.25° tile edge
(the synthetic fixture lies exactly on a tile corner) put probe points into
neighbouring tiles, and those tiles were then downloaded. Every tile is
requested with a ~200 m margin, so `assign_tiles` now reuses margins:

-   A point within half the margin of an edge may be served by a
    neighbouring tile that is already needed or cached.
-   A new tile is added only when no such tile covers the point.

This reduces downloads for any track along a tile edge. The values are
unchanged: same model, and bilinear interpolation inside the margin. The
COP30 regression values did not change.

## Evidence (reference activities)

| Track | Model | Median | 90 % below | Steepest | ≥ 30° |
|---|---|---|---|---|---|
| Activity B | COP30 / DGM1 | 19° / 19° | 34° / 34° | 48° / 51° | 3.8 / 3.9 km |
| Activity A | COP30 / DGM1 | 18° / 20° | 35° / 37° | 53° / 49° | 1.5 / 1.8 km |
| Activity C | COP30 / DGM1 | 13° / 15° | 26° / 36° | 48° / 54° | 0.8 / 1.6 km |
| Activity D | COP30 / DGM1 | 13° / 10° | 35° / 34° | 48° / 51° | 3.2 / 3.0 km |

-   Above the treeline (Activity A) both models agree within about 2°.
-   In forest (Activity D) COP30's canopy roughness makes gentle ground
    look steeper (median 13° vs 10°). This is the same effect that adds
    gain (ADR-013).
-   The 1 m model resolves short steep features that 30 m pixels smooth
    out (Activity C: 90th percentile 36° vs 26°).
-   With both models stored, prefer the terrain model (DGM1).

## Consequences

-   Phase 6 is complete. Terrain slope per profile bin, as a feature for
    the personal pace model (Phase 7), can be derived from the stored
    per-point series without re-sampling.
-   The slope bands are display bands, not thresholds with meaning.
    Changing them changes only the presentation.
