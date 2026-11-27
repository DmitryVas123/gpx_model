# ADR-011: HTTP API and local web page

-   **Status:** Accepted
-   **Date:** 2026-11-22
-   **Code:** `alpha.api`: `app.py` (wiring), `context.py`, `errors.py`,
    `request_log.py`, `reports.py`, `routers/`, `schemas.py`,
    `server.py`, `static/index.html`

## Context

Phase 5 exposes the use cases over HTTP and adds a minimal UI to inspect
profiles, comparisons, and scenarios. ALPHA stays single-user and
local-first, and the data (personal activities) is sensitive.

## Decision

-   **FastAPI + Pydantic v2 only in `alpha.api`.** `fastapi`, `pydantic`,
    `starlette`, and `uvicorn` are imported nowhere else (enforced by
    `tests/test_architecture.py`). Route handlers only translate HTTP to
    use cases; there are no calculations or SQL in them.
-   **One JSON shape for CLI and API.** `alpha.report` remains the only
    code that turns results into JSON. API response models in
    `schemas.py` mirror it with `extra="forbid"`, so any drift fails
    response validation in tests instead of silently changing the API.
    OpenAPI is generated from these models (`/docs`, `/openapi.json`).
-   **Endpoints:**

    | Method and path | Purpose |
    |---|---|
    | `GET /health` | Liveness and version |
    | `POST /files?filename=` | Import GPX sent as the **raw request body**. 201 created, 200 already imported. Raw body avoids a multipart dependency, and the browser sends a `File` directly. |
    | `GET /tracks` | List with summaries |
    | `GET /tracks/{id}` | Full analysis; analysis params as query parameters |
    | `PATCH /tracks/{id}` | Kind, elevation source, plan link (`inferred_by=user`) |
    | `DELETE /tracks/{id}/source-file[?dry_run=true]` | Delete the whole source file |
    | `GET /comparisons?a=&b=` | Comparison; a GET because nothing is created (analyses are only cached) |
    | `GET /tracks/{id}/scenarios` | DIN 33466 range; repeat `speed_multiplier` for a custom range; `assumptions=personal` (ADR-019) |
    | `GET /baseline` | Personal speed models and validation (ADR-016 to ADR-019) |
    | `GET /tracks/{id}/explanation`, `GET /baseline/explanation` | Explanations; `llm=true` asks the local Ollama model (ADR-020) |
    | `GET /dem/datasets` | DEM datasets and whether downloads are available (ADR-012) |
    | `POST /tracks/{id}/dem` | Sample, store, and compare DEM elevation; downloads only missing tiles (ADR-012) |
    | `GET /tracks/{id}/dem/{dataset}` | Elevation check from the stored profile; no network |

-   **Errors** always use `{"error": {"code", "message", "details?"}}`:

    | Status | Codes |
    |---|---|
    | 404 | `TRACK_NOT_FOUND` |
    | 409 | `INVALID_EDIT`, `DEM_PROFILE_MISSING` (ADR-012), `BASELINE_NOT_FITTED` (ADR-019) |
    | 413 | `FILE_TOO_LARGE` |
    | 502 | `DEM_DOWNLOAD_FAILED` (ADR-012) |
    | 503 | `DEM_UNAVAILABLE`: tiles not cached and no API key (ADR-012) |
    | 422 | `INVALID_REQUEST` (schema), `INVALID_PARAMS` (domain validation), `SCENARIO_INPUT`, or the GPX read code (`INVALID_XML`, `EMPTY_FILE`, …) |

-   **Logging:** one JSON line per request with operation ID (also
    returned as `X-Operation-Id`), method, path, status, and duration.
    The log never includes bodies, query strings, file names, or
    coordinates. Uvicorn's access log is disabled because it would log
    query strings.
-   **Local only by default:** `alpha serve` binds `127.0.0.1:8000`.
    There is no authentication; binding to another host prints a warning.
-   **UI:** a single static page (`/`) in plain JavaScript and inline
    SVG.
    -   No CDN, fonts, or external scripts, so it works offline and sends
        nothing elsewhere.
    -   All server data is inserted with `textContent`, never `innerHTML`,
        because GPX names are untrusted (tested).
    -   It shows provenance labels (calculated / estimated / observed /
        unknown) and the safety boundary.
-   **No background workers** (supersedes the planned ADR-006). Imports
    take ≤ 0.6 s for 25k points and requests take milliseconds; sync
    handlers run in FastAPI's thread pool, and the upload handler
    offloads the import to it. Revisit only with a measured need.

-   **Structure (2026-11-24):** one router per topic in
    `alpha.api.routers` (`meta`, `tracks`, `analysis`, `elevation`,
    `explanations`).
    -   `create_app` only wires them together with the error handlers
        (`errors.py`) and the request log (`request_log.py`).
    -   Shared state is one typed `ApiContext` (`context.py`) holding
        the workspace, the DEM sampler, and the model name with its lock.
        The OpenTopography key is a private field, left out of `repr`.
    -   Report building used by several routes lives in `reports.py`.
    -   The split left the OpenAPI document byte-for-byte unchanged.

## Consequences

-   The CLI and API produce identical JSON for the same request.
-   Not suitable for multi-user or network exposure without adding
    authentication. That is outside the MVP (PROJECT_VISION.md).
-   The UI is deliberately minimal: no map (that needs map tiles and
    their licences, post-MVP) and no build step.
