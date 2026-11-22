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
-   `persistence` (Phase 3): SQLite repositories. The only module that
    imports SQLAlchemy.
-   `application` (Phase 3+): use cases that coordinate domain,
    adapters, and persistence.
-   `cli` now, `api` later: thin entry points that call use cases.

Dependencies point inward: `cli/api → application → domain`;
`gpx/persistence → domain`. Domain never imports adapters.

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

Until Phase 3, the CLI calls domain functions and the gpx adapter
directly; there is no application layer yet.

## 3. Source tree by phase

### Phases 1--2 (analysis core)

``` text
gpx_model/
├── pyproject.toml
├── src/alpha/
│   ├── domain/
│   │   ├── track.py          # Track and its enums
│   │   ├── classify.py       # kind / elevation-source heuristics
│   │   ├── geodesy.py        # step distances
│   │   ├── elevation.py      # hysteresis ascent/descent
│   │   ├── profile.py        # resampling, profile bins, gradient
│   │   ├── timing.py         # elapsed/moving/stopped/gaps
│   │   ├── climbs.py         # climb sections
│   │   ├── quality.py        # warning detection
│   │   └── analysis.py       # AnalysisParams, analyse(track, params)
│   ├── gpx/
│   │   └── reader.py         # bytes -> list[Track] + warnings
│   └── cli.py                # `alpha analyse FILE`
├── tests/
│   ├── fixtures/             # small synthetic GPX, committed
│   ├── unit/
│   └── regression/           # golden outputs for reference files
├── scripts/
│   └── explore.py            # former main.py; pandas/plotly allowed here
├── data/
│   ├── samples/              # committed, shareable GPX (planned routes)
│   └── local/                # git-ignored; personal activities, SQLite DB
└── docs/adr/
```

### Phases 3--5 (added)

``` text
src/alpha/
├── application/              # import_file, analyse_track, compare, scenarios
├── persistence/              # SQLAlchemy models + repositories
├── scenarios/                # or domain/scenarios.py: din33466 method
└── api/                      # FastAPI, Pydantic request/response schemas
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

### Import (Phase 3)

1.  Read bytes; reject files over the size limit (default 50 MB).
2.  Compute SHA-256; if it exists, return the existing record.
3.  `gpx.reader` produces tracks and import warnings.
4.  Infer kind and elevation source heuristically; mark as inferred.
5.  Copy the file to `data/local/files/<sha256>.gpx`; persist source
    file, tracks, and points in one transaction.

### Analyse

1.  Load track; build `AnalysisParams` (defaults or user overrides).
2.  Return a cached analysis if `(track, algorithm_version, params_hash)`
    exists; otherwise call `analyse` and persist.

### Compare

1.  Ensure both tracks have analyses with the same version and params
    hash (recompute if needed).
2.  Build `ComparisonResult` from summaries, gradient distributions, and
    climb sections. Not persisted.

### Scenarios

1.  Load analysis; validate assumptions for the chosen method.
2.  Compute the range of scenarios with full breakdown; persist.

## 7. Technology

### MVP

-   Python 3.12+, `pyproject.toml`, virtual environment
-   gpxpy, NumPy, pyproj
-   SQLAlchemy 2.x + SQLite (Phase 3)
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
-   ADR-004: ascent/descent by hysteresis, 3 m for all sources.
-   ADR-007: moving time by 3D windowed speed (rev. 2, Strava-calibrated).
-   ADR-008: column-oriented `Track` with NumPy; pandas outside domain.
-   ADR-009: 50 m profile bins, no clipping, gradient bands, climbs.
-   ADR-010: jitter-filtered distance (3 m minimum displacement).

Planned:

-   ADR-002: SQLite first; PostGIS only for spatial queries (Phase 3).
-   ADR-003: GPX (`trk` and `rte`) as the only input format for MVP.
-   ADR-005: scenario method `din33466` and its units (Phase 4).
-   ADR-006: when to introduce background workers.
