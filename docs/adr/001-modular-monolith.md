# ADR-001: Modular monolith, layers introduced per phase

-   **Status:** Accepted
-   **Date:** 2026-11-22

## Context

ALPHA is a single-user, local-first tool built by one developer. The
first design proposed four layers (api, application, domain,
infrastructure) with ports, DTOs, Unit of Work, and Specification from
the start. At that point the only working code was a GPX-reading
script.

## Decision

-   One Python package (`src/alpha`), one process.
-   The dependency rule applies immediately: `alpha.domain` imports only
    the standard library, NumPy, pyproj (geodesic math, added in
    Phase 1), and itself; `gpxpy` is imported only in
    `alpha.gpx`. Both rules are enforced by `tests/test_architecture.py`.
-   Packages are created when the phase that needs them starts:
    `domain` and `gpx` in Phase 1, `cli` in Phase 1, `persistence` and
    `application` in Phase 3, `api` in Phase 5 (MVP_ROADMAP.md).
-   No abstract interfaces until a second implementation or a testing
    need exists.

## Consequences

-   Less scaffolding to maintain; the structure reflects working code.
-   Until Phase 3 the CLI calls domain functions and the GPX adapter
    directly. Introducing `application` later means moving that
    orchestration out of the CLI, a small and expected refactor.
-   The architecture test must be extended when new packages get rules
    (e.g. "only `persistence` imports SQLAlchemy").
