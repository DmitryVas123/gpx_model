# ADR-008: Column-oriented Track with NumPy; pandas outside the domain

-   **Status:** Accepted
-   **Date:** 2026-11-22

## Context

The exploration script stored points in a pandas DataFrame. The first
data-model draft proposed one Pydantic object per track point. Recorded
activities are sampled at 1 Hz: the reference Strava files have 13,473
and 24,827 points. Every metric is a per-step calculation (distance,
elevation change, duration) that is naturally vectorised.

Options considered:

1.  **List of point objects** (dataclass or Pydantic): clear, but slow
    for 25k points and every calculation becomes a Python loop.
2.  **pandas DataFrame in the domain:** vectorised, but column names and
    dtypes are not type-checked, index alignment can silently change
    results, and it is a heavy dependency for pure calculations.
3.  **Frozen dataclass holding NumPy arrays:** vectorised, typed field
    names, minimal dependency.

## Decision

Option 3. `Track` (DATA_MODEL.md §4) holds equal-length arrays
`lat_deg`, `lon_deg`, `ele_raw_m`, `time_utc_s`, `source_segment`.
Missing elevation and time are NaN. Domain functions take and return
these types.

pandas and plotting libraries are allowed only in `scripts/`
(installed via the `explore` extra).

## Consequences

-   Fast, deterministic, mypy-checkable calculations.
-   `Track` must validate in `__post_init__` that all arrays have the
    same length and expected dtypes, since NumPy will not.
-   Arrays inside a frozen dataclass are still mutable; domain code must
    not write to input arrays (consider `arr.setflags(write=False)` at
    construction).
-   Exploration code converts to a DataFrame when convenient.
