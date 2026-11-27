# ADR-010: Jitter-filtered distance

-   **Status:** Accepted (calibrated on two activities)
-   **Date:** 2026-11-22
-   **Code:** `alpha.domain.geodesy.displacement_pairs`,
    `filtered_step_distances_m`; used by `analysis` and `profile`

## Context

Version 0.1.0 summed every within-segment step. Strava's reported
distances for the two reference activities exposed a large bias:

| Activity | Strava | Sum of 1 Hz steps |
|---|---|---|
| Activity A | 8.30 km | 9.93 km (+20 %) |
| Activity B | 20.80 km | 23.68 km (+14 %) |

At 1 Hz, GPS position noise of about a metre is comparable to the
distance actually walked per second (median step 0.6–1.0 m). The
path length of a noisy line is always longer than the true path, so raw
summing systematically overstates distance. Most of the bias does not
come from detected stops (only 0.6–0.9 km), but from noise while walking
slowly.

The bias also flattened gradients. The 50 m profile bins were laid out
along the inflated distance axis, so each bin covered less real ground
than its nominal length.

## Options measured

Filtered distance against Strava:

| Method | Activity A | Activity B |
|---|---|---|
| Subsample every 5 s / 10 s | +3 % / −2 % | +1 % / −2 % |
| Rolling mean of positions, 10 s | −1 % | −2 % |
| **Minimum displacement 3 m** | **+0.3 %** | **+0.1 %** |
| Minimum displacement 2 m / 5 m | +3 % / −3 % | +2 % / −2 % |

On the planned routes (mapy.com, 9–15 m point spacing), the 3 m filter
changes distance by only 0.05–0.10 %.

## Decision

-   **Minimum-displacement filter:** within each source segment, the
    first point is accepted, and each later point is accepted once it is
    at least `distance_min_step_m` (3 m, horizontal) from the last
    accepted point. Distance is the geodesic sum between accepted points.
-   It is the canonical `distance_m` for **all** tracks, planned and
    recorded, so comparisons use one definition. It works in space, not
    time, so it does not depend on sampling rate.
-   `distance_3d_m` uses the same accepted points.
-   The **profile distance axis** uses the same filtered distance, so bins
    add up to `distance_m` and gradients are not flattened by jitter.
-   The unfiltered sum is kept as `distance_raw_m` for transparency.
-   `distance_min_step_m = 0` reproduces the unfiltered behaviour exactly
    (tested).

## Consequences

-   Distances match Strava within 0.3 % on both reference activities.
    One parameter fits two independent activities, which is encouraging
    but still a small sample.
-   A remainder shorter than 3 m at the end of each source segment is not
    counted (at most 3 m per segment).
-   **Limitation:** noise whose spread exceeds the threshold can still
    leak through. If the anchor point sits at one edge of a wide jitter
    cloud, points on the opposite edge are ≥ 3 m away and get accepted.
    Real stationary 1 Hz noise on the reference files stays within the
    threshold, but a poor-fix recording may not.
-   Very tight switchbacks (turns shorter than 3 m) are slightly cut.
-   **Recorded gradients rose as expected.** The steepest 50 m bin of
    Activity A went from 38 % (0.1.0) to 56 % (0.2.0). That bin climbs
    28 m in 185 s near 2,150 m; its jittery raw path is 73 m and the
    straight line 41 m, so 50 m of filtered distance is plausible. The
    planned route to the same summit peaks at 48–49 %. The sensitivity is
    real (2 m filter: 47 %, 5 m: 58 %), so single-bin maxima on recorded
    tracks should be read as approximate.
-   Revisit with GPS-only (non-barometric) recordings and other devices.
