# ALPHA --- MVP and Development Roadmap

## Delivery strategy

Build a tested analysis core before any infrastructure. React, LLMs,
Redis, PostGIS, and ML are out of scope until the MVP works end to end.

Phase exit criteria matter more than dates.

## Current state (2026-11-22)

-   **Phase 0 done.** Package skeleton (`src/alpha/domain`,
    `src/alpha/gpx`), `pyproject.toml`, `.gitignore`, ruff/mypy/pytest
    configured, architecture tests enforce the import rules, ADR-001 and
    ADR-008 written.
-   **Phase 1 done.** `Track` and enums (`domain/track.py`), WGS84 step
    distances with segment mask (`domain/geodesy.py`, pulled forward
    from Phase 2 because quality checks need it), kind/elevation-source
    heuristics (`domain/classify.py`), quality checks
    (`domain/quality.py`), GPX reader for `<trk>` and `<rte>` with
    encoding detection (`gpx/reader.py`), and `alpha inspect FILE`.
    93 tests; all four reference files meet the exit criteria.
-   **Phase 2 done.** Hysteresis ascent/descent (`domain/elevation.py`,
    ADR-004), profile bins, gradient, and gradient distribution
    (`domain/profile.py`, ADR-009), elapsed/moving/stopped/gap time
    (`domain/timing.py`, ADR-007), climb sections (`domain/climbs.py`),
    `analyse()` with `ALGORITHM_VERSION` and `params_hash`
    (`domain/analysis.py`), the JSON report (`report.py`), and
    `alpha analyse FILE [--json] [--profile-bin-m ...]`.
-   **Calibration (algorithm 0.2.0).** Strava's values for both
    activities exposed a +14–20 % distance bias from 1 Hz GPS jitter and
    a +8–21 % moving-time bias. A 3 m minimum-displacement distance
    filter (ADR-010) and a 3D windowed-speed moving rule (ADR-007 rev. 2)
    now match Strava within 0.3 % (distance) and 1 % (moving time).
    Strava's elevation gain cannot be reproduced from exported data
    (ADR-004).
-   **Phase 3 done.** SQLite storage with original files kept by SHA-256
    (`persistence/`, ADR-002), use cases (`application/tracks.py`),
    comparison (`domain/comparison.py`), and the CLI commands `import`,
    `list`, `show`, `compare`, `edit` (kind, elevation source, link to
    plan), `delete --yes`. The CLI is now a package (`cli/app.py`,
    `cli/render.py`). 344 tests, including persistence round trips,
    rollback, restart, architecture rules, and the exit-criterion
    comparison of `input.gpx` with `Nebelhorn_push.gpx`.
-   **Phase 4 done.** Scenario method registry with DIN 33466
    (`domain/scenarios.py`), assumptions with source (user / preset;
    `fitted` reserved for Phase 7), speed-multiplier range, breakdown and
    uncertainty notes. `alpha scenarios ID` shows observed times of the
    track itself or of linked activities, each with an implied speed
    multiplier (`application/scenarios.py`, ADR-005). Scenarios are not
    stored. 378 tests, including monotonicity invariants and the exit
    criterion on `input.gpx` + `Nebelhorn_push.gpx`.
-   **Phase 5 done — MVP complete.** FastAPI app in `alpha/api/` (routes
    call use cases only; Pydantic schemas validate `alpha.report` output
    with `extra="forbid"`), uniform error format, one JSON log line per
    request without personal data, local page at `/` (plain JS, inline SVG
    profile, compare, scenarios, edit/link; no external resources), and
    `alpha serve` on 127.0.0.1 (ADR-011). 432 tests, including the
    end-to-end API flow and checks that the page never uses `innerHTML`
    and loads nothing from the internet. The page itself was checked for
    JavaScript syntax but not yet rendered in a browser by the assistant.
-   **Phase 6 done (terrain enrichment, ADR-012 to ADR-015).**
    -   COP30 surface model via OpenTopography: opt-in, fixed 0.25° tiles,
        local cache, tile margins reused at edges.
    -   Bavaria DGM1 terrain model from local GeoTIFF tiles. Downloaded
        from the portal's metalink with `alpha get-tiles` (all tiles, or
        `--for-tracks`), each tile verified.
    -   Profiles sampled every 20 m with provenance and attribution
        (schema 3, additive upgrades).
    -   Analyses can run on any stored series (`--elevation dem:<id>`,
        algorithm 0.3.0).
    -   The elevation check covers offset, spread, drift
        (`ELEVATION_DRIFT`), and terrain slope by 10° band. It is
        available in the CLI (also `--all`), the API, and the page, with
        downloads only after explicit confirmation. Report format 6.
    -   Findings on four activities:
        -   Strava's gain equals COP30 sampled every 20 m.
        -   COP30 overstates gain in forest by 0–28 % against the terrain
            model (+235 m on Wanderung am Abend).
        -   The Nebelhorn barometer drifted by −40 m.
        -   The drift threshold of 20 m separates cleanly against the
            terrain model, but not against COP30 in forest.
    -   604 tests.
-   `scripts/explore.py` (former `main.py`): plotting sandbox only. Every
    known issue in its calculations is solved in the package (segment
    boundaries, spacing-independent windows, no clipping, timestamps,
    `<rte>`, encodings). Its numbers are not authoritative.
-   Reference data: two planned routes (mapy.com) in `data/samples/`
    and four recorded activities (Strava) in `data/local/`, with DGM1
    tiles for them; see
    DATA_MODEL.md §3.
-   Open questions (owner's input needed):
    -   confirm that mapy.com exports may be shared publicly; otherwise
        move them to `data/local/` (their golden files move with them);
    -   `.idea/` is ignored now but still tracked from earlier commits
        (`git rm -r --cached .idea` untracks it);
    -   more calibration data: the distance and moving-time rules fit two
        activities from one device. Strava values for a few more
        (different terrain, ideally another device) would show whether
        they generalise.

------------------------------------------------------------------------

## Phase 0 --- Foundation

### Tasks

-   `pyproject.toml`, `src/alpha/`, virtual environment.
-   Ruff, pytest, mypy or pyright; a single `make check`-style command.
-   `.gitignore`: `data/local/`, `*.db`, `.env`, `.idea/`, `__pycache__/`.
-   Move personal Strava files to `data/local/`. Move planned routes to
    `data/samples/` if their export licence permits; otherwise also
    `data/local/`.
-   Move `main.py` to `scripts/explore.py` as the exploration sandbox.
-   ADR-001, ADR-008.

### Exit criteria

-   Fresh clone installs and runs (empty) tests.
-   No personal activity file is tracked by Git.

## Phase 1 --- GPX reading and quality checks

### Tasks

-   Domain types: `Track`, `TrackKind`, `ElevationSource`, `Inference`,
    `QualityWarning`.
-   `gpx.reader`: bytes → tracks, reading `<trk>` (all `<trkseg>`) and
    `<rte>`; keep `creator` and `<type>`.
-   Kind and elevation-source heuristics, marked as inferred.
-   Quality checks from DATA_MODEL.md §8.
-   Synthetic fixtures: empty file, one point, two segments with a gap,
    missing elevation on some points, partial timestamps, duplicate
    points, a single GPS jump, Cyrillic track name, `<rte>`-only file.
-   CLI: `alpha inspect FILE` prints tracks, point counts, inferred
    kind/elevation source, and warnings.

### Exit criteria

-   All four reference files load; mapy.com files are inferred
    `PLANNED/PLANNER_DEM`, Strava files `ACTIVITY` with timestamps.
-   Invalid input gives structured warnings or errors, never a crash.
-   The original bytes are never modified.

## Phase 2 --- Deterministic analysis

### Tasks

-   Geodesic step distance, masked at source-segment boundaries.
-   Hysteresis ascent/descent (ADR-004).
-   Resampling to profile bins; bin gradient; outlier flagging without
    clipping (ADR-009).
-   Time metrics for activities: elapsed, moving, stopped, recording
    gaps (ADR-007).
-   Climb sections and gradient distribution.
-   `analyse(track, params) -> Analysis` with `algorithm_version` and
    `params_hash`.
-   CLI: `alpha analyse FILE [--profile-bin-m 50 ...]` prints summary,
    climbs, warnings; optional JSON output.
-   Regression tests: golden JSON outputs for the reference files (the
    personal ones run only when present in `data/local/`).

### Exit criteria

-   Each metric in DATA_MODEL.md §5 has a unit test with a hand-checked
    synthetic fixture.
-   Invariants tested: distance is unchanged when points are
    duplicated; ascent − descent ≈ end − start elevation; reversing a
    track swaps ascent and descent; results do not depend on point
    density beyond a documented tolerance (resample a fixture at 1 m
    and 15 m).
-   Same input and params → byte-identical JSON output.
-   Planned routes report time metrics as unknown, not zero.

## Phase 3 --- Persistence and comparison

### Tasks

-   SQLite schema from DATA_MODEL.md §10 with SQLAlchemy 2.x.
-   Import use case: SHA-256 idempotency, stored copy, one transaction.
-   User overrides: kind, elevation source, `planned_track_id` link.
-   `ComparisonResult` (DATA_MODEL.md §7.1).
-   CLI: `alpha import`, `alpha list`, `alpha compare A B`.

### Exit criteria

-   Data survives restart; importing the same file twice is a no-op.
-   Comparing `input.gpx` with `Nebelhorn_push.gpx` (same summit,
    different path) shows the distance, ascent, gradient-band, and climb
    differences, and marks time metrics of the planned route as
    unknown.
-   Repositories are tested against a temporary SQLite file; domain
    tests do not touch the database.

## Phase 4 --- Scenario engine

### Tasks

-   `din33466` method with explicit assumptions and labelled preset
    (DATA_MODEL.md §7.2).
-   Speed multipliers produce a faster/baseline/slower range.
-   Full breakdown and uncertainty notes in every result.
-   CLI: `alpha scenarios TRACK --preset din --break-min 30`.

### Exit criteria

-   Monotonic tests: lower speed never shortens a scenario; breaks add
    exactly their duration.
-   Output never shows a single number without assumptions.
-   For a planned route with a linked activity, the output shows the
    recorded moving time next to the scenarios, labelled as observed.

## Phase 5 --- API and minimal UI

### Tasks

-   FastAPI endpoints (ARCHITECTURE.md §8); Pydantic schemas at the
    boundary.
-   Minimal UI only if it helps inspect profiles and comparisons (a
    static page with a chart is enough).
-   Structured logging without coordinates; documented error responses.

### Exit criteria

-   End-to-end: import → analyse → link activity to plan → compare →
    scenarios.
-   No business logic in route handlers.

------------------------------------------------------------------------

## Post-MVP

### Phase 6 --- Terrain enrichment

-   ✅ DEM provider adapter; sample elevation along tracks
    (`ElevationSource.DEM_ENRICHED`).
-   ✅ Record DEM source, resolution, and vertical datum.
-   ✅ Compare recorded/planner elevation with DEM; surface discrepancies.
-   ✅ Analyse with the DEM series as an explicit, hashed parameter choice.
-   ✅ Enrichment and elevation check from the API and page; batch
    enrichment (`alpha enrich --all`).
-   ✅ National terrain model from local tiles (Bavaria DGM1); forest
    effect measured (ADR-013).
-   ✅ Drift of recorded elevation against the model (ADR-014).
-   ✅ Terrain slope from raster, clearly separated from track gradient
    (ADR-015).

**Phase 6 exit:** every DEM-derived value carries its dataset, surface
or terrain type, and attribution. Downloads happen only on explicit
request and never send the track (COP30), or send only what the user
chose (DGM1 metalink). Recorded elevation is never modified. The findings
are reproducible from regression tests that skip when personal data is
absent.

### Phase 7 --- Personal performance baseline

-   Dataset from activities only; profile bins with moving time are the
    natural training rows (gradient → vertical/horizontal speed).
-   New package `alpha.modelling` (never inside `domain`, ADR-005): reads
    stored analyses via the application layer and returns
    `DinAssumptions(source=fitted)` with sample count and error range,
    which feed the existing `din33466` method unchanged.
-   First baseline: fit personal DIN parameters from past activities
    (the implied speed multipliers of Phase 4 are the simplest form) and
    report their spread; only then try ML. A genuinely different model,
    such as pace per gradient band, becomes a new registered method.
-   Route-grouped or chronological validation; report sample count and
    error ranges.
-   Show "not enough data" until a documented minimum of activities
    exists. Two activities are not enough.

### Phase 8 --- AI explanation layer

-   LLM receives typed analysis results and assumptions, not raw
    coordinates; opt-in.
-   Explains, never calculates; structured output referencing metric
    names; validated against the analysis.

### Phase 9 --- Platform

-   Map UI, background processing, PostGIS, deployment: only when
    justified by use and measurement.

## First backlog

1.  Phase 0 housekeeping: package, tooling, `.gitignore`, move data
    files.
2.  `Track` type and `gpx.reader` with synthetic fixtures.
3.  Quality checks and `alpha inspect`.
4.  Step distance with segment masking.
5.  Hysteresis ascent/descent.
6.  Profile bins and gradient.
7.  Activity time metrics.
8.  Climb sections, gradient distribution, `analyse`, golden tests.
9.  Persistence only after the analysis output is stable.
