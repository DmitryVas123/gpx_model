# ADR-007: Moving time, stops, and stationary GPS jitter

-   **Status:** Accepted, revision 2 (calibrated on two activities)
-   **Date:** 2026-11-22 (rev. 1 and rev. 2)
-   **Code:** `alpha.domain.timing`

## Context

Recorded activities spend a large share of time standing still: 25–34 %
of 1 Hz steps in the reference files have zero length. Moving time feeds
later pace modelling, so its definition must be explicit and stable.

## Decision (revision 2, algorithm 0.2.0)

Every second of an activity is assigned to exactly one category, so
`elapsed = moving + stopped + recording_gap` holds exactly.

-   **Recording gap:** a step longer than `gap_threshold_s` (60 s), or
    any step crossing a source-segment boundary.
-   **Windowed speed** at each point: 3D straight-line displacement
    between the first and last point of a centred time window of
    `moving_window_s` (20 s), divided by the time between them.
    -   The window is defined in seconds, not points, so it is
        independent of sampling rate.
    -   The window never reaches across a recording gap.
    -   Using displacement rather than summed step lengths cancels jitter
        while standing.
    -   Using 3D keeps slow but steep climbing (e.g. 0.05 m/s horizontal,
        0.4 m/s vertical) counted as moving.
-   **Moving:** a non-gap step whose starting point's windowed speed
    exceeds `moving_min_speed_m_s` (0.3 m/s).
-   **Stopped:** every other non-gap step.
-   `stop_count`: runs of stopped steps lasting at least
    `stop_min_duration_s` (60 s). Shorter pauses still count as stopped
    time, just not as a "stop".
-   `distance_while_stopped_m`: jitter-filtered distance (ADR-010)
    credited during stopped steps. It is included in `distance_m`.
-   Planned routes, partial timestamps, or time running backwards: all
    time metrics are `None` (unknown).

## Evidence

Strava's moving time is an independent reference that the owner
provided for both activities:

| Method | Activity A (Strava 148.4 min) | Activity B (Strava 301.5 min) |
|---|---|---|
| Rev. 1: stop radius 10 m / 60 s | 179 (+21 %) | 326 (+8 %) |
| Speed > 0.3 m/s, 2D, 20 s window | 145 (−2 %) | 300 (−0.5 %) |
| **Speed > 0.3 m/s, 3D, 20 s window** | **149 (+0.5 %)** | **304 (+0.8 %)** |
| Speed > 0.25 / 0.35 m/s, 3D, 20 s | 160 / 138 | 311 / 295 |
| Speed > 0.3 m/s, 3D, 10 s / 30 s | 146 / 152 | 300 / 305 |

The threshold matters most: ±0.05 m/s moves the result by about ±7 %
(Activity A) or ±3 % (Activity B). The window length matters little.

### Revision 1 (superseded)

A stop was a run of at least 60 s staying within 10 m (3D) of its first
point, with the walk into and out of the radius trimmed. It
overestimated moving time by 8–21 % against Strava. A 10 m radius over
60 s means anything slower than 0.17 m/s, which is a much lower bar than
Strava's. Radius and duration also interacted in ways that were hard to
explain.

## Consequences

-   Moving time matches Strava within 1 % on both reference activities.
    One threshold fits two activities, so this is still a small sample.
-   **Limitation:** sustained very slow movement (< 0.3 m/s in 3D, e.g.
    careful scrambling or deep snow) counts as stopped. That is
    consistent with Strava, but it understates effort time on technical
    terrain.
-   Each pause edge is blurred by up to half a window (≤ 10 s).
-   Moving time is always shown with its parameters (the CLI does), and
    comparisons are valid only under the same `params_hash`.
-   Performance: vectorised per gap-free run, a few milliseconds for 25k
    points.
