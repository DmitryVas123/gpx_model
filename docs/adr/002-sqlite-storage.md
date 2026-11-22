# ADR-002: Local SQLite storage, original files, cached analyses

-   **Status:** Accepted
-   **Date:** 2026-11-22
-   **Code:** `alpha.persistence`, `alpha.application.tracks`

## Context

Phase 3 needs imported tracks to survive restarts, duplicate imports to be
detected, and comparisons to run without re-reading GPX files. ALPHA is
single-user and local-first: one person, one machine, tens to hundreds
of tracks of up to ~25k points each.

## Decision

-   **One data directory** (default `data/local/`, overridable with
    `--data-dir` or `ALPHA_DATA_DIR`) holding:
    -   `alpha.db`, the SQLite database;
    -   `files/<sha256>.gpx`, an unmodified copy of every imported file.

    The original file is the source of truth; everything in the database
    can be rebuilt from it.
-   **SQLAlchemy 2.x**, imported only in `alpha.persistence` (enforced by
    `tests/test_architecture.py`). Foreign keys are enabled per connection
    so deletes cascade.
-   **Idempotent import by SHA-256** of the file bytes. Importing the same
    bytes again changes nothing, even under a different name.
-   **Transaction order:** parse first (unreadable files write nothing),
    then copy the file, then one database transaction for the source file,
    tracks, points, and the default analysis. If the transaction fails, a
    newly written copy is removed again.
-   **Integer IDs** rather than UUIDs. The tool is single-user and IDs are
    typed on the command line (`alpha compare 1 3`).
-   **Points as rows** in a `WITHOUT ROWID` table keyed by
    `(track_id, idx)`. This is fast enough at this scale (a 25k-point
    import takes about 0.6 s including analysis), and loading needs no GPX
    re-parse.
-   **Analyses are cached** per `(track, algorithm_version, params_hash)`,
    with profile bins and climb sections in their own tables. Missing
    analyses are computed on demand (`list`, `show`, `compare`), so a
    version bump simply leads to recomputation. A cached analysis must
    serialise byte-identically to a fresh one (tested).
-   **User corrections** of kind or elevation source are stored with
    `inferred_by = user`. They delete the track's cached analyses, whose
    warnings mention the old values.
-   **Deleting** works per source file: all its tracks, points, analyses,
    and the stored copy. Links from activities to deleted plans are set to
    NULL. The CLI requires `--yes`.
-   **No general migrations.** `meta.schema_version` is checked on open.
    Purely additive versions (new tables only, listed in
    `ADDITIVE_UPGRADES`) are applied in place; anything else is refused
    with instructions to move the database aside and re-import (the
    originals are kept). Schema 2 (`dem_profiles`, ADR-012) is additive.
-   No bounding-box columns or spatial index until a feature needs them.

## Consequences

-   Single-writer use only; concurrent access from several processes is
    not designed for.
-   Every schema change needs either a version bump with re-import or
    Alembic migrations. Introduce Alembic when re-importing becomes
    inconvenient.
-   PostGIS remains a later option (ARCHITECTURE.md §7) if spatial queries
    appear.
