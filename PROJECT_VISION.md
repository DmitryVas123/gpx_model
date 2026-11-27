# ALPHA --- Project Vision

## 1. Product vision

ALPHA is a personal mountain decision-intelligence platform. It helps a
user understand a route's measurable characteristics, compare it with
previous activity, explore how assumptions affect estimated completion
time, and inspect the uncertainty behind those estimates.

ALPHA does **not** choose a route on behalf of the user. It makes route
and performance information easier to reason about.

**Where it stands (2026-11-24):**
-   The MVP (§6) is complete.
-   So are the first post-MVP capabilities: terrain models, personal
    speeds, and plain-language explanations (§8).

See [MVP_ROADMAP.md](MVP_ROADMAP.md) for details.

## 2. Problem

Route-planning tools commonly show a line on a map, distance, and
elevation profile. Those values do not necessarily explain:

-   where the steep or sustained sections are;
-   how a proposed route compares with routes the user has completed;
-   how sensitive an estimated duration is to pace assumptions or
    breaks;
-   which parts of an analysis are measured, inferred, or uncertain.

ALPHA aims to connect route geometry with a user's own historical
activity and transparent scenario assumptions.

## 3. Target user

Initial target: one technically curious mountain hiker who records
activities as GPX and wants to analyse routes and personal performance.

The first release is single-user and local-first. Multi-user accounts,
social features, and public route sharing are outside the initial scope.

## 4. Core user journeys

### Journey A --- Analyse a completed activity

1.  Import a recorded GPX activity (e.g. a Strava export).
2.  Review data-quality warnings (recording gaps, GPS jumps, missing
    data).
3.  See distance, elevation gain/loss, altitude range, and
    elapsed/moving/stopped time.
4.  Inspect the elevation profile, gradient per profile bin, and climb
    sections.
5.  Save the activity for later comparison.

### Journey B --- Analyse a planned route

1.  Import a planned route (GPX `<trk>` or `<rte>`, e.g. from a route
    planner).
2.  Confirm or correct the inferred kind and elevation source.
3.  Inspect the elevation profile, gradient distribution, and climb
    sections (where the steep or sustained parts are).
4.  Choose a scenario method and set explicit speed and break
    assumptions.
5.  Generate a range of estimated durations, with assumptions and
    breakdown visible.

### Journey C --- Compare a proposed route with personal history

1.  Select a planned route.
2.  Select one or more completed activities, optionally linking an
    activity to the plan it followed.
3.  Compare distance, ascent, descent, gradient distribution, and the
    steepest climb sections.
4.  Identify where the new route differs materially from past
    experience. Time metrics of the planned route are shown as
    unknown, and scenario estimates are labelled as estimates.

### Journey D --- Learn your own pace (post-MVP)

1.  Mark activities that were unusually hard or easy.
2.  See personal speeds fitted from the normal ones, with how many
    routes they rest on and how far off they were for routes left out.
    The answer is "not enough data" until there are enough.
3.  Estimate a planned route with those speeds and your own variation
    between hikes as the range.
4.  Read the result explained in plain language.

Terms such as profile bin, climb section, and elevation source are
defined in [DATA_MODEL.md](DATA_MODEL.md) §2.

## 5. Product principles

1.  **Deterministic first:** geometry and arithmetic must be computed by
    tested Python code, not an LLM.
2.  **Explainable outputs:** every metric has a definition, unit, and
    provenance.
3.  **Uncertainty is explicit:** estimates are ranges or scenarios, not
    false-precision predictions.
4.  **Personalisation must be earned:** do not claim a personal model is
    reliable until sufficient representative data exists.
5.  **Safety over engagement:** no "safe/unsafe" verdict, no avalanche
    inference, and no encouragement to continue in dangerous conditions.
6.  **Local-first:** user GPX files and activity history stay on the
    user's computer. Network access happens only on explicit request:
    -   terrain tiles, which never include the track itself;
    -   a language model running locally.
7.  **Modular evolution:** begin with a modular monolith; extract
    workers/services only when a measured need appears.

## 6. MVP definition

The MVP answers one narrow question:

> What are the measurable characteristics of this GPX route, and how
> does it compare with my recorded activities?

### Included

-   GPX import (`<trk>` and `<rte>`) and data-quality warnings.
-   Planned/activity and elevation-source classification (inferred,
    user-correctable).
-   Distance and ascent/descent with a documented noise policy.
-   Elevation profile and gradient per profile bin.
-   Climb sections and gradient distribution.
-   Elapsed, moving, and stopped time for activities.
-   Local persistence in SQLite.
-   Comparison of two tracks (planned or recorded).
-   Rule-based duration scenarios (DIN 33466 first) using user-provided
    assumptions.
-   CLI or minimal API for interacting with the analysis core.
-   Automated tests and sample GPX fixtures.

### Excluded from MVP

-   LLM-generated advice. After the MVP, a local model may only
    **reword** ALPHA's own explanations, checked against the report. It
    gives no advice.
-   ML prediction trained on personal activity. After the MVP, personal
    speeds are transparent fits with validation, not ML.
-   Weather and avalanche integrations.
-   Live navigation or turn-by-turn guidance.
-   Automatic route discovery.
-   Social features or user accounts.
-   Multi-agent architecture.
-   Distributed queues and microservices.
-   Claims about technical mountaineering difficulty or route safety.

## 7. Success criteria

The MVP is successful when:

-   a valid GPX can be imported reproducibly;
-   malformed or incomplete GPX data produces actionable validation
    errors;
-   calculations have documented definitions and unit tests;
-   the same input produces the same analysis output;
-   a user can compare two activities without manual spreadsheet work;
-   scenario estimates expose all assumptions and do not imply
    certainty.

## 8. Longer-term product direction

After the MVP, ALPHA may evolve through these capabilities:

| Capability | Status |
|---|---|
| Terrain raster enrichment using a suitable elevation model | ✅ Copernicus GLO-30 and Bavaria DGM1; terrain slope (Phase 6) |
| A personal performance baseline | ✅ fitted speeds with validation and effort (Phase 7) |
| Statistical duration modelling with uncertainty intervals | ✅ ranges from the user's own variation (Phase 7) |
| Scenario simulation for pace, breaks, and delays | ✅ speed multipliers, breaks, delay (Phase 4) |
| AI-generated explanations grounded only in computed analysis | ✅ templates, optionally reworded by a local model and validated (Phase 8) |
| More detailed classification of route sections | open |
| A map-centric web UI | open (Phase 9, when justified) |

## 9. Risk and limitations

-   GPX elevation may be noisy, missing, barometrically corrected, or
    derived from a map.
-   GPS tracks can contain gaps, duplicates, spikes, and poor sampling.
-   Elevation gain depends on smoothing and calculation choices.
-   Hiking time depends on terrain, weather, snow, load, fitness,
    route-finding, and breaks.
-   A model trained on a small set of the user's hikes can overfit and
    generalize poorly.
-   Slope derived from track-point elevation is not the same as terrain
    slope from a high-resolution DEM. ALPHA reports both separately.
-   Elevation models differ: a surface model adds treetops in forest, and
    a barometer can drift. ALPHA shows the series side by side instead of
    choosing one silently.
-   Technical difficulty, exposure, rock/ice conditions, and avalanche
    danger cannot be inferred reliably from a GPX line alone.

Every UI and report should distinguish **observed**, **calculated**,
**estimated**, and **unknown** information.
