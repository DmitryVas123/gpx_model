# ALPHA --- Architecture

## 1. Style and rules

A **modular monolith**: one Python package (`src/alpha/`), one process,
no background workers (ADR-001, ADR-011). Domain logic is independent of
frameworks, databases, file formats, and the network. Packages are added
when a phase needs them, never ahead of time.

### Packages and what they may import

| Package | Role | May import | Enforced |
|---|---|---|---|
| `domain` | Types and pure calculations: analysis, comparison, scenario formulas, reference elevation, terrain slope | stdlib, NumPy, pyproj | ✅ |
| `gpx` | GPX bytes → `Track` and read warnings | gpxpy (only here) | ✅ |
| `persistence` | SQLite schema, repository, file store | SQLAlchemy (only here) | ✅ |
| `dem` | Elevation models: datasets, tiles, OpenTopography download, local GeoTIFF tiles, metalinks, sampling | rasterio (only here), network | ✅ |
| `modelling` | Pure fitting of personal speeds from samples | stdlib, NumPy, `domain` | ✅ |
| `application` | Use cases: coordinate domain, adapters, and storage; own transactions | everything above; never `cli`, `api`, `report` | ✅ |
| `report` | The only place JSON reports are built | `application` and below, `explain` | — |
| `explain` | Facts, template explanations, validation of model text | stdlib only | ✅ |
| `llm` | Local language model (Ollama on a loopback address) | network (stdlib `urllib`) | ✅ |
| `cli` | Argument parsing and plain-text rendering; reads `.env` | everything | — |
| `api` | FastAPI routes and Pydantic schemas mirroring `report` | FastAPI, Pydantic, Starlette, uvicorn (only here) | ✅ |

"Enforced" means `tests/test_architecture.py` fails when the rule is
broken. Network libraries are allowed only in `dem` and `llm`. Neither
`gpx` nor `persistence` imports `application`, `cli`, or `report`.

Entry points contain no calculations or SQL: `cli/render.py` only
formats; API route handlers only translate HTTP to use cases.

## 2. Components

``` mermaid
flowchart TD
    CLI["cli"] --> App["application (use cases)"]
    API["api + local page"] --> App
    CLI --> Report["report (JSON)"]
    API --> Report
    Report --> Explain["explain (facts, templates, validator)"]
    CLI --> LLM["llm (local Ollama)"]
    API --> LLM
    App --> Domain["domain"]
    App --> Modelling["modelling (fitting)"]
    App --> GPX["gpx (gpxpy)"]
    App --> Repo["persistence (SQLite)"]
    App --> DEM["dem (rasterio, tiles)"]
    Modelling --> Domain
    GPX --> Domain
    Repo --> Domain
    Repo --> DB[("alpha.db")]
    App --> Files["files/<sha256>.gpx"]
    DEM --> Tiles["dem/<dataset>/ tiles"]
    DEM -. opt-in .-> OT["OpenTopography / bayernwolke.de"]
    LLM -. opt-in, loopback .-> Ollama["Ollama on this computer"]
```

`alpha inspect` and `alpha analyse FILE` call the GPX adapter and the
domain directly (nothing is stored). Every stored-track command goes
through `application`.

## 3. Source tree

``` text
gpx_model/
├── pyproject.toml
├── src/alpha/
│   ├── domain/
│   │   ├── track.py               # Track, TrackKind, ElevationSource, Inference, Effort
│   │   ├── classify.py            # kind / elevation-source heuristics
│   │   ├── quality.py             # QualityParams, warnings, check_track
│   │   ├── geodesy.py             # step distances, jitter filter
│   │   ├── elevation.py           # hysteresis ascent/descent
│   │   ├── profile.py             # profile bins, gradient, distribution
│   │   ├── timing.py              # elapsed / moving / stopped / gaps
│   │   ├── climbs.py              # climb sections
│   │   ├── analysis.py            # AnalysisParams, analyse(), ALGORITHM_VERSION
│   │   ├── comparison.py          # compare(a, b) -> ComparisonResult
│   │   ├── scenarios.py           # method registry, din33466, assumptions
│   │   ├── reference_elevation.py # DEM knots, comparison, drift
│   │   └── terrain.py             # terrain slope from DEM probes
│   ├── gpx/reader.py              # bytes -> tracks + warnings
│   ├── persistence/
│   │   ├── schema.py              # tables, SCHEMA_VERSION, additive upgrades
│   │   ├── repository.py          # Database, Repository, records
│   │   └── files.py               # FileStore: files/<sha256>.gpx
│   ├── dem/
│   │   ├── datasets.py            # COP30, BY_DGM1 with attribution
│   │   ├── tiles.py               # fixed 0.25° grid, tile assignment
│   │   ├── opentopography.py      # download (API key never logged)
│   │   ├── provider.py            # cached-tile sampler, ElevationSampler
│   │   ├── local.py               # local GeoTIFF tiles, footprint index
│   │   ├── metalink.py            # metalink parsing and tile download
│   │   └── sampling.py            # bilinear sampling
│   ├── modelling/
│   │   ├── baseline.py            # model 1: speed multiplier; effort helpers
│   │   └── split.py               # model 2: flat and vertical factors
│   ├── application/
│   │   ├── tracks.py              # Workspace; import/list/show/compare/edit/delete
│   │   ├── scenarios.py           # estimate_duration, estimate_personal
│   │   ├── elevation.py           # enrich_track, elevation_check, get_tiles
│   │   └── baseline.py            # personal_baseline, personal_assumptions
│   ├── report.py                  # every JSON report (shared by CLI and API)
│   ├── explain/
│   │   ├── facts.py               # Fact, FactSet
│   │   ├── templates.py           # scenarios / elevation / baseline templates
│   │   ├── validate.py            # numbers, citations, completeness, topics
│   │   └── explain.py             # template -> optional model -> validation
│   ├── llm/ollama.py              # OllamaRewriter (loopback only, CPU retry)
│   ├── cli/
│   │   ├── app.py                 # commands and exit codes
│   │   ├── render.py              # plain-text output
│   │   └── config.py              # .env / environment (API key; never printed)
│   └── api/
│       ├── app.py                 # create_app: context, handlers, routers
│       ├── context.py             # ApiContext (workspace, DEM, model) + dependencies
│       ├── errors.py              # ApiError, exception -> status mapping
│       ├── request_log.py         # one privacy-preserving log line per request
│       ├── reports.py             # use case -> report, shared by routes
│       ├── routers/               # meta, tracks, analysis, elevation, explanations
│       ├── schemas.py             # Pydantic models (extra="forbid")
│       ├── server.py              # uvicorn runner (alpha serve)
│       └── static/index.html      # local page: plain JS + inline SVG
├── tests/
│   ├── fixtures/                  # small synthetic GPX
│   ├── unit/                      # domain and modelling, no database
│   ├── integration/               # reader, persistence, use cases, DEM, CLI, API, explain
│   ├── regression/                # golden outputs, reference-file checks
│   └── test_architecture.py       # import rules (§1)
├── scripts/
│   ├── explore.py                 # old exploration sandbox; pandas/plotly allowed here
│   ├── eval_explain.py            # validator and local-model evaluation (ADR-020)
│   └── build_site_docs.py         # collects the docs for the site (mkdocs.yml)
├── mkdocs.yml                     # documentation site; deployed by .github/workflows/docs.yml
├── data/
│   └── local/                     # git-ignored: all real GPX, alpha.db, files/, dem/, golden/
└── docs/adr/                      # decision records (index: docs/adr/README.md)
```

## 4. Key design decisions

| Decision | Why | ADR |
|---|---|---|
| **Column-oriented `Track`** (NumPy arrays, not point objects) | 25k points per long activity; vectorised, deterministic | 008 |
| **Geodesic steps masked at source-segment boundaries** | A recording gap is never distance | 010 |
| **Windows in metres or seconds**, never point counts | Point spacing ranges from 0.6 m (1 Hz) to 15 m (planner) | 007, 009 |
| **Versioned, parameterised analysis** (`algorithm_version`, `params_hash`) | Results are comparable only with the same calculation and options | 002 |
| **Read GPX as bytes**; the XML declaration decides the encoding | Text mode breaks non-ASCII names on Windows; the same bytes are hashed | — |
| **Scenario methods vs assumptions**: formulas in a registry; assumptions carry `user` / `preset` / `fitted` | A personal model supplies assumptions to the same formula | 005 |
| **Elevation series as a parameter** (`recorded` or `dem:<dataset>`) | Never a silent fallback; DEM values always carry attribution | 012 |
| **Fitting outside the domain**, as pure functions on samples | Testable with synthetic data; the domain stays model-free | 016 |
| **Templates first; a model may only reword**, validated against facts | No calculation, no invention, free and local | 020 |

Patterns are used only where they solve a present problem:
-   adapters (`gpx`, `persistence`, `dem`, `llm`);
-   a function registry for scenario methods;
-   a repository;
-   constructor arguments for dependencies;
-   Pydantic DTOs only at the API boundary;
-   a `Protocol` where two implementations exist (`ElevationSampler`).

There are no abstract base classes with a single implementation.

## 5. Use-case flows

All in `alpha.application`; each use case owns its transaction.

-   **Import:**
    1.  SHA-256 of the bytes; an existing file is a no-op.
    2.  Parse; reject before writing anything.
    3.  Store the original.
    4.  In one transaction: source file, tracks, points, and the default
        analysis. On failure the new copy is removed.
-   **Show, list, compare:** look up the cached analysis for
    `(track, algorithm_version, params_hash)`, otherwise analyse and cache.
    With `elevation=dem:<id>` the stored DEM series replaces the recorded
    one for that analysis (an error if it is not stored). Comparisons
    analyse both tracks with the same options and are not stored.
-   **Edit:** user corrections are stored with `inferred_by = user`;
    links are validated (activity → planned route). Effort goes to
    `track_annotations`. Cached analyses are dropped on a kind or
    elevation-source change.
-   **Scenarios:**
    1.  Analysis plus assumptions go into the registered method, once
        per speed multiplier.
    2.  Observed times are added: the track's own, and those of linked
        activities.
    3.  `estimate_personal` first fits the baseline with the same
        analysis options and fails when nothing is fitted.
-   **Enrich:**
    1.  Sample the dataset at 20 m knots, plus terrain-slope probes, in
        one pass over the tiles.
    2.  Store the profile and slope.
    3.  Drop cached analyses.
    4.  Compare with the recorded elevation.

    Tiles come from the cache, or are downloaded only on request.
-   **Baseline:**
    1.  Collect one sample per eligible activity; exclusions carry a
        reason.
    2.  Pass the samples to `alpha.modelling`.
    3.  Compare activities outside the effort selection with the fit.
-   **Explain:**
    1.  The entry point builds the same JSON report as `--json`.
    2.  `alpha.explain` builds facts and the template; with `--llm`, it
        asks the local model and validates the answer.

## 6. Technology

| In use | Where |
|---|---|
| Python 3.12+, `pyproject.toml` | everywhere |
| NumPy, pyproj | domain, modelling, dem |
| gpxpy | `gpx` only |
| SQLAlchemy 2 + SQLite | `persistence` only |
| rasterio (GDAL) | `dem` only |
| FastAPI, Pydantic v2, uvicorn | `api` only |
| Ollama (external program, HTTP on loopback; no Python dependency) | `llm`, opt-in |
| pytest, Ruff, mypy (strict) | development |
| pandas, plotly | `scripts/` only |

Considered only with a measured need: scikit-learn (inside `modelling`,
if transparent models stop being enough), PostGIS, and a map UI
(Phase 9).

## 7. HTTP API (ADR-011)

`alpha serve` (default `127.0.0.1:8000`) serves the API, OpenAPI docs at
`/docs`, and the local page at `/`.

| Method and path | Purpose |
|---|---|
| `GET /health` | Liveness and version |
| `POST /files?filename=` | Import GPX sent as the raw body (201 new / 200 already imported) |
| `GET /tracks` | Stored tracks with summaries and `dem_datasets` |
| `GET /tracks/{id}` | Full analysis; analysis options as query parameters |
| `PATCH /tracks/{id}` | `kind`, `elevation_source`, `planned_track_id`, `clear_planned_track`, `effort`, `clear_effort` |
| `DELETE /tracks/{id}/source-file[?dry_run=true]` | Delete the source file and its tracks |
| `GET /comparisons?a=&b=` | Compare b against a |
| `GET /tracks/{id}/scenarios` | Duration range and observed times; `assumptions=personal` for fitted speeds |
| `GET /baseline` | Personal speed models |
| `GET /dem/datasets` | DEM datasets and whether downloads are available |
| `POST /tracks/{id}/dem` | Sample, store, and compare DEM elevation (downloads missing tiles only) |
| `GET /tracks/{id}/dem/{dataset}` | Elevation check from the stored profile (no network) |
| `GET /tracks/{id}/explanation?topic=scenarios\|elevation` | Plain-language explanation; `llm=true` rewords locally |
| `GET /baseline/explanation` | The same for the personal speeds |

-   **Analysis options:** `profile_bin_m`, `ascent_threshold_m`,
    `distance_min_step_m`, `moving_min_speed_m_s`, `moving_window_s`,
    `climb_min_gradient_percent`, and `elevation=recorded|dem:<dataset>`.
-   **Scenario options:** `horizontal_speed_km_h`, `ascent_rate_m_h`,
    `descent_rate_m_h`, `break_min`, `delay_min`, repeatable
    `speed_multiplier`, `assumptions`, and `effort`.
-   **Errors:** always `{"error": {"code", "message", "details?"}}`:

| Status | Codes |
|---|---|
| 404 | `TRACK_NOT_FOUND` |
| 409 | `INVALID_EDIT`, `DEM_PROFILE_MISSING`, `BASELINE_NOT_FITTED` |
| 413 | `FILE_TOO_LARGE` |
| 422 | `INVALID_REQUEST`, `INVALID_PARAMS`, `SCENARIO_INPUT`, GPX read codes |
| 502 | `DEM_DOWNLOAD_FAILED` |
| 503 | `DEM_UNAVAILABLE` |

The CLI reads the OpenTopography key and the model name and passes them
to `create_app(data_dir, dem_api_key, llm_model)`. `alpha.api` never
reads the environment.

## 8. Reliability and observability

-   One JSON log line per HTTP request: operation ID, method, path,
    status, duration. It never contains bodies, query strings, file
    names, or coordinates.
-   Import is idempotent by SHA-256; schema upgrades are additive and
    automatic.
-   Raw and processed elevation are separate; `ele_raw_m` is never
    modified.
-   Golden outputs pin results for the reference files. They live in
    `data/local/golden/` with the files themselves and are skipped when
    absent.
-   Requests take milliseconds and a 25k-point import about 0.6 s, so
    there are no background workers. A local-model explanation takes
    15–30 s and runs only on request, one at a time.

## 9. Security and privacy

-   Personal activities, the database, tiles, and `.env` live in
    `data/local/` or are git-ignored. No real GPX file is committed,
    including planned routes; only synthetic test fixtures are.
-   Start and end points can reveal home or accommodation: they are
    never logged, and coordinates never appear in reports or logs.
-   **Network access is opt-in and limited to two adapters:**
    -   `dem`, COP30 (`alpha enrich`, or the page after a confirmation):
        fixed 0.25° tiles; the track itself is never sent (ADR-012).
    -   `dem`, DGM1 (`alpha get-tiles --yes`): the region the user chose,
        or with `--for-tracks` the 1 km squares the tracks cross. The
        metalink is treated as untrusted: plain file names only, known
        HTTPS hosts only, every tile verified (ADR-013).
    -   `llm` (`--llm`, or "Reword" on the page): only an Ollama server
        on a loopback address; any other host is refused. The model
        receives facts from the reports, never coordinates or GPX text
        (ADR-020).
-   The OpenTopography key comes from the environment or `.env`. It is
    never printed, logged, stored, or put into error messages.
-   Model text is shown only after validation; caveats and the safety
    boundary are fixed text from ALPHA.
-   The API binds to localhost and has no authentication. The page loads
    nothing from the internet and inserts server data as text only.
-   Deleting a source file removes all derived rows and the stored copy.
-   Elevation data carries its licence attribution in every output.

## 10. Decision records

All decisions with their reasoning and measurements are listed in
[docs/adr/README.md](docs/adr/README.md).
