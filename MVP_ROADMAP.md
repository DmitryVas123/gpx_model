# ALPHA --- Roadmap and Status

## Delivery strategy

-   Build a tested, deterministic analysis core first; add storage,
    interfaces, models, and AI only on top of it, one phase at a time.
-   Phase exit criteria matter more than dates. A phase is done when its
    criteria are met and documented, not when its task list is empty.
-   Infrastructure waits for a measured need (Phase 9).

## Current state (2026-11-27)

| Phase | Status | Delivered | ADRs |
|---|---|---|---|
| 0 Foundation | ✅ | Package, tooling, `.gitignore`, architecture tests | 001, 008 |
| 1 GPX reading and quality | ✅ | `Track`, GPX reader (`<trk>`, `<rte>`, encodings), heuristics, quality warnings, `alpha inspect` | 008 |
| 2 Deterministic analysis | ✅ | Distance, ascent, profile bins, time metrics, climb sections, versioned `analyse()`, JSON report, `alpha analyse` | 004, 007, 009, 010 |
| 3 Persistence and comparison | ✅ | SQLite, original files by hash, cached analyses, `import/list/show/edit/delete`, `compare` | 002 |
| 4 Scenario engine | ✅ | DIN 33466 method registry, assumptions with source, ranges, observed times | 005 |
| 5 API and minimal UI | ✅ | FastAPI, error format, request log, local page, `alpha serve` (**MVP complete**) | 011 |
| 6 Terrain enrichment | ✅ | Copernicus GLO-30 and Bavaria DGM1, analysis on DEM elevation, elevation check, drift warning, terrain slope | 012–015 |
| 7 Personal performance baseline | ✅ | `alpha.modelling`: speed multiplier and flat/vertical factors, route-grouped validation, effort annotation, `--personal` scenarios | 016–019 |
| 8 AI explanation layer | ✅ | Template explanations; local Ollama model rewording with validation; CLI, API, page | 020 |
| 9 Platform | not started | Map UI, background work, deployment: only when use justifies it | — |

Versions: algorithm `0.3.0`, report format `12`, database schema `4`.
The test suite has about 870 tests, including architecture rules and
golden outputs.

Reference data:
-   two planned routes (mapy.com) and seven recorded activities
    (Strava), all in `data/local/`, as no real GPX is shared. Five
    activities are normal effort and two are marked hard;
-   COP30 for all of them and DGM1 for the Bavarian ones
    (DATA_MODEL.md §3).

Since Phase 8, local profiles and a synthetic `dev` profile with
generated hikes of known pace, plus a simulation study of the speed
models (ADR-021).

## Open items

### Needs data or the owner's input

-   **More normal-effort routes.** Three more activate the flat/vertical
    model (8 routes, ADR-017). Steep normal-effort hikes help most: in
    the simulation two steep routes among 8 did as much for steep
    predictions as 12 more similar routes (ADR-021).
-   **Moving threshold for speed fits: checked, unchanged.** On real
    hikes, only 0–5 min of climbing per hike is at 0.15–0.3 m/s; that
    band is mostly rests. Recheck if very steep normal-effort hikes are
    added (ADR-021).
-   **One-way routes.** Separate ascent and descent rates need several;
    today they share one factor (ADR-017).
-   **An explicit effort factor** needs about three hard and five normal
    efforts on varied terrain (ADR-018).
-   **Strava reference values** (distance, moving time, gain) for
    activities 3–7. The distance and moving-time rules are calibrated on
    two activities from one device (ADR-007, ADR-010).

### Optional improvements

-   Explanations for plan-vs-activity comparison and track analysis,
    using the same pattern: facts, template, caveats (ADR-020).
-   German explanations tried with the real model, and Ollama
    `keep_alive` to avoid the load delay on the first request.
-   Revisit provisional thresholds as data grows:
    -   drift 20 m (ADR-014);
    -   baseline gates of 5 and 8 routes (ADR-016, ADR-017);
    -   moving speed 0.3 m/s (ADR-007).

## Phases

### Phase 0 --- Foundation ✅

Package skeleton (`src/alpha/`), `pyproject.toml`, ruff/mypy/pytest,
`.gitignore` for personal data, architecture tests, the exploration
script moved to `scripts/explore.py`.

Exit criteria met: a fresh clone installs and runs the tests; no
personal activity file is tracked by Git.

### Phase 1 --- GPX reading and quality checks ✅

Column-oriented `Track` and enums, GPX reader for `<trk>` (all
`<trkseg>`) and `<rte>` with encoding detection, kind and
elevation-source heuristics marked as inferred, quality warnings
(DATA_MODEL.md §8), synthetic fixtures for the edge cases, and
`alpha inspect`.

Exit criteria met:
-   all reference files load;
-   planned routes are inferred `planned`/`planner_dem`, activities
    `activity` with timestamps;
-   invalid input gives structured errors, never a crash;
-   original bytes are never modified.

### Phase 2 --- Deterministic analysis ✅

-   Geodesic distance with a jitter filter (ADR-010), hysteresis
    ascent (ADR-004), 50 m profile bins and unclipped gradient
    (ADR-009), elapsed/moving/stopped time (ADR-007), climb sections,
    gradient distribution.
-   `analyse(track, params)` with `ALGORITHM_VERSION` and `params_hash`,
    the JSON report, and golden regression outputs.
-   **Calibration** against Strava: distance within 0.3 %, moving time
    within 1 % on both reference activities.

Exit criteria met:
-   every metric has a hand-checked unit test;
-   invariants hold (duplicates, reversal, resampling);
-   byte-identical JSON for the same input;
-   planned routes report time as unknown.

### Phase 3 --- Persistence and comparison ✅

SQLite schema with SQLAlchemy 2, original files stored by SHA-256
(idempotent import), cached analyses, user corrections and plan links,
the comparison of two tracks.

Exit criteria met:
-   data survives restart;
-   re-import is a no-op;
-   Plan 1 vs Activity A shows the distance, ascent,
    gradient-band, and climb differences, with the plan's time unknown.

### Phase 4 --- Scenario engine ✅

DIN 33466 as a registered method; assumptions carry their source
(`user`, `preset`, later `fitted`); a speed-multiplier range; breakdown
and uncertainty notes; observed times of the track or of linked
activities with an implied speed multiplier.

Exit criteria met:
-   slower assumptions never give shorter durations;
-   breaks add exactly their duration;
-   no result without its assumptions;
-   observed times are labelled as observed.

### Phase 5 --- API and minimal UI ✅ (MVP complete)

FastAPI with schemas mirroring `alpha.report`, uniform errors, one
privacy-preserving log line per request, a self-contained local page,
and `alpha serve` on `127.0.0.1`.

Exit criteria met:
-   end-to-end import → analyse → link → compare → scenarios;
-   no business logic in route handlers.

### Phase 6 --- Terrain enrichment ✅

-   **Copernicus GLO-30** via OpenTopography: opt-in, fixed 0.25° tiles
    (the track is never sent), local cache. Sampled every 20 m; stored
    with provenance and attribution (ADR-012).
-   **Bavaria DGM1** from local GeoTIFF tiles, downloadable from the
    portal's metalink and verified tile by tile (ADR-013).
-   Any analysis can run on DEM elevation (`--elevation dem:<id>`), as a
    hashed parameter and never as a fallback.
-   The elevation check reports:
    -   offset and spread;
    -   **drift** (`ELEVATION_DRIFT`, ADR-014);
    -   **terrain slope** in 10° bands (ADR-015).
-   Findings:
    -   Strava's gain equals COP30 sampled every 20 m.
    -   COP30 overstates gain in forest by up to 28 % against DGM1.
    -   One barometer drifted by −40 m on the ascent of Activity A.

Exit criteria met:
-   every DEM-derived value carries its dataset, model type, and
    attribution;
-   downloads only on explicit request;
-   recorded elevation is never modified;
-   findings are pinned by regression tests that skip without personal
    data.

### Phase 7 --- Personal performance baseline ✅

-   `alpha.modelling` is pure fitting on samples; the application layer
    collects them (ADR-016).
-   **Model 1:** one speed multiplier on DIN 33466.
    -   Fitted in log space, one weight per route.
    -   95 % ranges for the average and for a single new hike.
    -   Leave-one-route-out validation.
    -   Gate of 5 routes (ADR-016).
-   **Model 2:** flat and vertical factors, with gates of 8 routes and a
    route mix that varies by ×1.5 (ADR-017).
-   **Effort** is the user's annotation. Fits use normal efforts by
    default, and other efforts are compared with the fit (ADR-018).
-   `--personal` scenarios use the better-validated model. The range is
    the user's own variation, and there is no fallback (ADR-019).
-   **Today:** model 1 is fitted on 5 routes (×1.46, typical error 3 %);
    model 2 waits for 3 more routes.

Exit criteria met:
-   transparent baseline before any ML;
-   route-grouped validation with sample counts and errors;
-   "not enough data" below documented minimums.

ML stays deferred: the transparent models already predict within a few
percent.

### Phase 8 --- AI explanation layer ✅

-   Templates turn each report (scenarios, elevation check, baseline)
    into facts and statements. They are the default: exact, instant,
    free.
-   `--llm`: a local Ollama model rewords the draft. It is accepted only
    if:
    -   every number and duration matches a fact;
    -   cited facts are stated;
    -   nothing is left out;
    -   forbidden topics are absent.

    Caveats are never reworded. CLI, API, and page.
-   The model sees facts only: no coordinates and no GPX text (ADR-020).

-   **Adversarial hardening (2026-11-27):**
    -   A catalogue of 30 attacks in 6 categories, and
        `scripts/eval_explain.py` (`--templates`, `--attacks`, `--live`,
        comparing models by acceptance and latency).
    -   False acceptance fell from 73 % to 0 % (420 attacks on the
        user's reports).
    -   The language is an allow-list, closing a prompt-injection path
        (ADR-020).

Exit criteria met:
-   explains, never calculates;
-   opt-in;
-   structured output referencing metric names;
-   validated against the report.

### Phase 9 --- Platform

Map UI, background processing, PostGIS, and deployment, each only
when justified by use and measurement. Nothing is planned yet.
