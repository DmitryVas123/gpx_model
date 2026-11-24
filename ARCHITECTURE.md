# ALPHA --- Architecture and Engineering Design

## 1. Architectural style

A **modular monolith**: one Python package, one process. Domain logic is
independent of frameworks, databases, and file formats.

Layers are introduced **when they are needed**, not up front. The
dependency rule applies from day one; empty folders and interfaces do
not.

### Dependency direction

-   `domain`: core types (DATA_MODEL.md §4, §6, §7) and pure
    calculations. Imports only the standard library, NumPy, and pyproj
    (pure geodesic math, no I/O).
-   `gpx`: adapter that turns GPX bytes into domain `Track` objects and
    import warnings. The only module that imports `gpxpy`.
-   `persistence`: SQLite schema, repository, and file store. The only
    package that imports SQLAlchemy.
-   `application`: use cases that coordinate domain, the GPX adapter, and
    persistence; each owns its transaction.
-   `report`: JSON report format (output boundary).
-   `cli` now, `api` later: thin entry points that call use cases and
    render results.

Dependencies point inward: `cli/api → application → domain`;
`gpx/persistence → domain`. Domain never imports adapters.
`tests/test_architecture.py` enforces: domain imports only stdlib,
NumPy, pyproj; gpxpy only in `gpx`; SQLAlchemy only in `persistence`;
`persistence` and `gpx` never import `application`, `cli`, or `report`;
`application` never imports `cli`.

## 2. Component diagram

``` mermaid
flowchart TD
    CLI["CLI (Phase 1+)"] --> App["Application use cases (Phase 3+)"]
    API["FastAPI (Phase 5)"] --> App
    App --> Domain["Domain: Track, Analysis, Comparison, Scenario"]
    App --> GPX["gpx adapter (gpxpy)"]
    App --> Repo["persistence (SQLite)"]
    GPX --> Domain
    Repo --> Domain
    Repo --> DB[("SQLite")]
    App --> Files["data/local/ originals"]
    App -. post-MVP .-> DEM["DEM provider"]
    App -. post-MVP .-> ML["Performance model"]
    App -. post-MVP .-> LLM["Explanation provider"]
```

`alpha inspect` and `alpha analyse FILE` still call the GPX adapter and
domain directly (nothing is stored); every stored-track command goes
through `application`.

## 3. Source tree by phase

### Current (MVP, Phases 1--5, Phase 6 in progress)

``` text
gpx_model/
├── pyproject.toml
├── src/alpha/
│   ├── domain/
│   │   ├── track.py          # Track and its enums
│   │   ├── classify.py       # kind / elevation-source heuristics
│   │   ├── geodesy.py        # step distances, jitter filter
│   │   ├── elevation.py      # hysteresis ascent/descent
│   │   ├── profile.py        # resampling, profile bins, gradient
│   │   ├── timing.py         # elapsed/moving/stopped/gaps
│   │   ├── climbs.py         # climb sections
│   │   ├── quality.py        # warning detection
│   │   ├── analysis.py       # AnalysisParams, analyse(track, params)
│   │   ├── comparison.py     # compare(a, b) -> ComparisonResult
│   │   ├── scenarios.py      # method registry, din33466, assumptions
│   │   ├── reference_elevation.py # DEM knots, interpolation, comparison,
│   │   │                     # drift (ADR-014)
│   │   └── terrain.py        # terrain slope from DEM probes (ADR-015)
│   ├── gpx/
│   │   └── reader.py         # bytes -> tracks + warnings
│   ├── persistence/
│   │   ├── schema.py         # SQLAlchemy tables (schema version)
│   │   ├── repository.py     # Database, Repository, records
│   │   └── files.py          # FileStore: files/<sha256>.gpx
│   ├── application/
│   │   ├── tracks.py         # Workspace; import/list/show/compare/edit/delete
│   │   ├── scenarios.py      # estimate_duration + observed times
│   │   ├── elevation.py      # enrich_track, elevation_check (DEM)
│   │   └── baseline.py       # personal_baseline: samples -> modelling
│   ├── explain/              # Phase 8: facts, templates, validator (pure, ADR-020)
│   ├── llm/                  # Phase 8: local Ollama adapter (loopback only)
│   ├── modelling/            # Phase 7: pure fitting on samples; imports only
│   │   │                     # numpy + domain (ADR-005)
│   │   ├── baseline.py       # model 1: one speed multiplier (ADR-016)
│   │   └── split.py          # model 2: flat and vertical factors (ADR-017)
│   ├── dem/                  # DEM adapter: datasets, 0.25° tiles, cache,
│   │                         # OpenTopography client, local GeoTIFF tiles
│   │                         # (local.py), metalink downloads (metalink.py),
│   │                         # bilinear sampling, open_dem()
│   ├── report.py             # JSON reports (shared by CLI and API)
│   ├── cli/
│   │   ├── app.py            # argument parsing, commands, exit codes
│   │   ├── render.py         # plain-text output
│   │   └── config.py         # .env / environment (API key; never printed)
│   └── api/
│       ├── app.py            # FastAPI routes, error handlers, request log
│       ├── schemas.py        # Pydantic response/request models
│       ├── server.py         # uvicorn runner (`alpha serve`)
│       └── static/index.html # local page: plain JS + inline SVG
├── tests/
│   ├── fixtures/             # small synthetic GPX, committed
│   ├── unit/                 # domain only, no database
│   ├── integration/          # reader, persistence, application, CLI, API
│   └── regression/           # golden outputs, exit-criterion checks
├── scripts/
│   └── explore.py            # former main.py; pandas/plotly allowed here
├── data/
│   ├── samples/              # committed, shareable GPX (planned routes)
│   └── local/                # git-ignored; personal GPX, alpha.db, files/, golden/
└── docs/adr/
```

### Planned

Further Phase 7 models (three DIN speeds, pace per gradient band) are
added to `alpha.modelling` (ADR-016).

## 4. Key design decisions

### 4.1 Column-oriented track

`Track` holds NumPy arrays (`lat_deg`, `lon_deg`, `ele_raw_m`,
`time_utc_s`, `source_segment`), not one object per point. A long
activity has 25k points at 1 Hz; vectorised step calculations stay fast
and deterministic. pandas is used only in `scripts/`.

### 4.2 Distances and boundaries

Step distances use a geodesic on the WGS84 ellipsoid (`pyproj.Geod`).
Every per-step calculation is masked at source-segment boundaries so a
recording gap is never counted as distance.

### 4.3 Windows in metres and seconds

All smoothing, resampling, and detection windows are expressed in
metres or seconds. Point spacing ranges from 0.6 m (Strava, 1 Hz) to
15 m (mapy.com planner), so point-count windows would give different
results for the same terrain.

### 4.4 Versioned, parameterised analysis

`analyse(track, params) -> Analysis` is a pure function. Each result
records `algorithm_version` and `params_hash`. Bump the version when a
calculation changes in a way that alters outputs; regression tests on
the reference files make such changes visible.

### 4.5 Scenario methods as strategies

A **method** is a pure formula registered by name in
`domain.scenarios.SCENARIO_METHODS` (`din33466` first). **Assumptions**
are inputs that record their source: `user`, `preset`, or `fitted`. A
personal model therefore usually supplies assumptions to an existing
method rather than replacing it. Fitting and any ML live in a separate
`alpha.modelling` package (Phase 7) that depends on the domain, never the
other way round (ADR-005). It is pure: `alpha.application.baseline`
collects samples from storage and passes them in (ADR-016). No class
hierarchy is needed.

### 4.6 Reading GPX

Read files as **bytes** (`"rb"`). The XML declaration decides the
encoding; text-mode reading on Windows defaults to cp1252 and breaks on
non-ASCII names such as Cyrillic route titles. The same bytes are
hashed (SHA-256) for idempotent import. Both `<trk>` and `<rte>` are
read; waypoints are kept as named points for later display.

## 5. Patterns

Use patterns only where they solve a present problem.

| Pattern | Where | When |
|---|---|---|
| Adapter | `gpx/`, later DEM/LLM | Now |
| Strategy (function registry) | Scenario methods | Phase 4 |
| Repository | `persistence/` | Phase 3 |
| Dependency injection (constructor args) | Use cases | Phase 3 |
| DTO (Pydantic) | API boundary only | Phase 5 |

Not planned for the MVP: Unit of Work beyond a SQLAlchemy session,
Specification, Factory, abstract base classes for single
implementations.

## 6. Use-case flows

Implemented in `alpha.application.tracks` (ADR-002).

### Import

1.  Compute SHA-256 of the bytes; if it exists, return the existing
    record (no-op).
2.  `gpx.reader` parses (size limit 50 MB). Unreadable files raise
    before anything is written.
3.  Copy the original to `<data dir>/files/<sha256>.gpx`.
4.  One transaction: source file, tracks with inferred kind/elevation
    source, points, and the default analysis. On failure the new copy is
    removed.

### Show / list

1.  Load the track; look up the analysis for `(track, ALGORITHM_VERSION,
    params_hash)`.
2.  If missing, call `analyse` and cache it.

### Compare

1.  Analyse both tracks with the same params (cached or computed).
2.  `domain.comparison.compare` builds the `ComparisonResult`. Not
    persisted.

### Edit / delete

-   Edit stores user corrections (`inferred_by = user`), validates links
    (activity → planned route), and drops cached analyses on kind or
    elevation-source changes.
-   Delete removes a whole source file with its tracks and the stored
    copy, only when confirmed.

### Scenarios

1.  Load the cached (or compute the) analysis; validated assumptions
    come from the caller (preset or user).
2.  Apply the registered method once per speed multiplier; result with
    breakdown and notes.
3.  Add observed times: the track's own if it is an activity, and those
    of every activity linked to it, each with its implied speed
    multiplier. Not persisted (ADR-005).

## 7. Technology

### MVP

-   Python 3.12+, `pyproject.toml`, virtual environment
-   gpxpy, NumPy, pyproj
-   SQLAlchemy 2.x + SQLite (ADR-002)
-   rasterio (GDAL) for reading DEM tiles, only in `alpha.dem` (ADR-012)
-   FastAPI + Pydantic v2 (Phase 5)
-   pytest, Ruff, mypy or pyright
-   pandas, plotly: exploration scripts only

### Later

-   PostGIS if spatial queries appear
-   scikit-learn for personal performance baselines, only inside
    `alpha.modelling` (ADR-005)
-   React + MapLibre UI
-   LLM provider behind an adapter, opt-in

## 8. HTTP API (ADR-011)

`alpha serve` (default `127.0.0.1:8000`) serves the API, OpenAPI docs at
`/docs`, and the local page at `/`.

| Method and path | Purpose |
|---|---|
| `GET /health` | Liveness and version |
| `POST /files?filename=` | Import GPX sent as raw body (201 new / 200 already imported) |
| `GET /tracks` | Stored tracks with summaries |
| `GET /tracks/{id}` | Full analysis; analysis params as query parameters |
| `PATCH /tracks/{id}` | `{kind, elevation_source, planned_track_id, clear_planned_track}` |
| `DELETE /tracks/{id}/source-file[?dry_run=true]` | Delete the source file and its tracks |
| `GET /comparisons?a=&b=` | Compare b against a |
| `GET /tracks/{id}/scenarios` | DIN 33466 range + observed times; `assumptions=personal` for fitted speeds |
| `GET /baseline` | Personal speed models (ADR-016 to ADR-019) |
| `GET /tracks/{id}/explanation?topic=` | Plain-language explanation; `llm=true` rewords locally (ADR-020) |
| `GET /baseline/explanation` | The same for the personal speeds |
| `GET /dem/datasets` | DEM datasets, `downloads_available` |
| `POST /tracks/{id}/dem` | Enrich with DEM elevation (downloads missing tiles only) |
| `GET /tracks/{id}/dem/{dataset}` | Elevation check from the stored profile (no network) |

Scenario query parameters:

-   `horizontal_speed_km_h`, `ascent_rate_m_h`, `descent_rate_m_h` (empty
    = DIN 33466 standard preset);
-   `break_min`, `delay_min`;
-   `speed_multiplier` (repeatable).

Analysis query parameters include `elevation=recorded|dem:<dataset>`
(ADR-012). The page has a matching selector.

Errors are always `{"error": {"code", "message", "details?"}}` with
404 / 409 / 413 / 422 (ADR-011); 409 includes `DEM_PROFILE_MISSING`;
DEM downloads add 502 `DEM_DOWNLOAD_FAILED` and 503 `DEM_UNAVAILABLE`.

The OpenTopography key is read by the CLI (`alpha serve`) and passed to
`create_app(data_dir, dem_api_key)`; `alpha.api` never reads the
environment for it.

## 9. Reliability and observability

-   One structured JSON log line per HTTP request (operation ID, method,
    path, status, duration); never bodies, query strings, file names, or
    coordinates.
-   Idempotent import by SHA-256.
-   Raw and processed elevation are separate arrays and columns.
-   Regression tests pin outputs for the reference GPX files.
-   Warnings returned whenever source data is incomplete.
-   No background workers: a 25k-point import with analysis takes about
    0.6 s and requests take milliseconds. Revisit only with a measurement
    (ADR-011).

## 10. Security and privacy

-   Personal activities (Strava exports) live in `data/local/`, which is
    git-ignored together with `*.db` and `.env`.
-   Committed GPX is limited to synthetic fixtures and planned routes
    whose export licence allows it.
-   Activity start/end points can reveal home or accommodation; never
    log or share them by default.
-   External LLM calls (post-MVP) are opt-in and receive computed
    metrics, not raw coordinates.
-   Deleting a source file removes all derived rows and the stored copy.
-   The API binds to localhost by default and has no authentication; the
    web page loads nothing from the internet and inserts data as text only.
-   Network access, always opt-in, happens only in two adapters:
    -   `alpha.llm` (`alpha explain --llm`) talks to a **local** Ollama
        server on a loopback address and refuses any other host
        (ADR-020);
    -   DEM download in `alpha.dem`:
    -   `alpha enrich` (COP30): fixed 0.25° tiles, never the track itself;
    -   `alpha get-tiles --yes` (DGM1 from a metalink): the region the user
        chose, or with `--for-tracks` the 1 km squares the tracks cross
        (ADR-013).
    The API key comes from the environment or `.env` and is never printed
    or logged (ADR-012).
-   Document licences and attribution for map and elevation data.

## 11. Architecture decision records

`docs/adr/NNN-title.md`, one page each:

Written (accepted):

-   ADR-001: modular monolith; layers introduced per phase.
-   ADR-002: SQLite, original files by hash, cached analyses, integer IDs.
-   ADR-004: ascent/descent by hysteresis, 3 m for all sources.
-   ADR-007: moving time by 3D windowed speed (rev. 2, Strava-calibrated).
-   ADR-008: column-oriented `Track` with NumPy; pandas outside domain.
-   ADR-009: 50 m profile bins, no clipping, gradient bands, climbs.
-   ADR-005: scenario methods vs assumption sources; ML outside domain.
-   ADR-010: jitter-filtered distance (3 m minimum displacement).
-   ADR-011: HTTP API, error format, logging, local page; no workers.
-   ADR-012: DEM enrichment (COP30 via OpenTopography, tiles, 20 m knots).

Planned:

-   ADR-003: GPX (`trk` and `rte`) as the only input format for MVP.
