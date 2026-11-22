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

### Current (Phases 1--3)

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
│   │   └── comparison.py     # compare(a, b) -> ComparisonResult
│   ├── gpx/
│   │   └── reader.py         # bytes -> tracks + warnings
│   ├── persistence/
│   │   ├── schema.py         # SQLAlchemy tables (schema version)
│   │   ├── repository.py     # Database, Repository, records
│   │   └── files.py          # FileStore: files/<sha256>.gpx
│   ├── application/
│   │   └── tracks.py         # Workspace; import/list/show/compare/edit/delete
│   ├── report.py             # JSON reports
│   └── cli/
│       ├── app.py            # argument parsing, commands, exit codes
│       └── render.py         # plain-text output
├── tests/
│   ├── fixtures/             # small synthetic GPX, committed
│   ├── unit/                 # domain only, no database
│   ├── integration/          # reader, persistence, application, CLI
│   └── regression/           # golden outputs, exit-criterion checks
├── scripts/
│   └── explore.py            # former main.py; pandas/plotly allowed here
├── data/
│   ├── samples/              # committed, shareable GPX (planned routes)
│   └── local/                # git-ignored; personal GPX, alpha.db, files/, golden/
└── docs/adr/
```

### Phases 4--5 (planned)

``` text
src/alpha/
├── domain/scenarios.py       # din33466 method (Phase 4)
├── application/scenarios.py  # scenario use cases (Phase 4)
└── api/                      # FastAPI, Pydantic request/response schemas (Phase 5)
```

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

Each duration method is a pure function with its own assumptions type
(`din33466` first). A method registry maps names to functions. No class
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

1.  Load analysis; validate assumptions for the chosen method.
2.  Compute the range of scenarios with full breakdown; persist.

## 7. Technology

### MVP

-   Python 3.12+, `pyproject.toml`, virtual environment
-   gpxpy, NumPy, pyproj
-   SQLAlchemy 2.x + SQLite (ADR-002)
-   FastAPI + Pydantic v2 (Phase 5)
-   pytest, Ruff, mypy or pyright
-   pandas, plotly: exploration scripts only

### Later

-   Rasterio/GDAL for DEM sampling; PostGIS if spatial queries appear
-   scikit-learn for personal performance baselines
-   React + MapLibre UI
-   LLM provider behind an adapter, opt-in

## 8. API sketch (Phase 5)

-   `GET /health`
-   `POST /files` (upload GPX) → source file + tracks
-   `GET /tracks`, `GET /tracks/{id}`
-   `PATCH /tracks/{id}` (set kind, elevation source, planned_track_id)
-   `GET /tracks/{id}/analysis?profile_bin_m=50&...`
-   `POST /comparisons` `{track_a_id, track_b_id}`
-   `POST /tracks/{id}/scenarios`

Example scenario request:

``` json
{
  "method": "din33466",
  "assumptions": {
    "horizontal_speed_m_h": 4000,
    "ascent_rate_m_h": 300,
    "descent_rate_m_h": 500,
    "break_s": 1800,
    "preset_name": "DIN 33466 standard"
  },
  "speed_multipliers": [0.8, 1.0, 1.2]
}
```

## 9. Reliability and observability

-   Structured logs with track ID and operation; never coordinates.
-   Idempotent import by SHA-256.
-   Raw and processed elevation are separate arrays and columns.
-   Regression tests pin outputs for the reference GPX files.
-   Warnings returned whenever source data is incomplete.
-   No background workers: a 25k-point analysis is expected to take well
    under a second. Revisit only with a measurement (ADR-006).

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
-   ADR-010: jitter-filtered distance (3 m minimum displacement).

Planned:

-   ADR-003: GPX (`trk` and `rte`) as the only input format for MVP.
-   ADR-005: scenario method `din33466` and its units (Phase 4).
-   ADR-006: when to introduce background workers.
