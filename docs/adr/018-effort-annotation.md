# ADR-018: Effort annotation and effort-aware baselines (Phase 7, step 3)

-   **Status:** Accepted
-   **Date:** 2026-11-24
-   **Code:** `alpha.domain.track.Effort`, table `track_annotations`,
    `alpha edit --effort`, `PATCH /tracks/{id}`, `alpha baseline --effort`

## Context

The speed models (ADR-016, ADR-017) treat every activity as a sample of
the same pace. The user reports that Activity A and Activity B were
**hard** efforts. A baseline for planning should describe ordinary hikes.
Pushing hard is not terrain, and ALPHA cannot detect it from the GPX
(heart rate is not imported, and pace alone confounds effort with
terrain).

## Decision

-   **`Effort` = `easy` | `normal` | `hard`, set only by the user**
    (`alpha edit ID --effort hard`, `none` clears it; `PATCH` with
    `effort` / `clear_effort`). ALPHA never infers it. It applies to
    activities only.
-   **Storage:** table `track_annotations` (`track_id` primary key,
    cascade; `effort`; `updated_at_utc`), schema 4 by an additive upgrade.
    It is kept apart from `tracks`, so imported data stays as read, and
    the table can take further user annotations. Track records and their
    JSON carry `effort` (report format 9).
-   **Baseline selection:** `BaselineParams.effort`, also
    `alpha baseline --effort`:

    | Selection | Fitted on |
    |---|---|
    | `normal` (default) | `normal` and not stated |
    | `hard` | `hard` only (a "pushing" pace) |
    | `easy` | `easy` only |
    | `all` | everything, ignoring the annotation |

    Untagged activities count as normal, which is the most common case
    and needs no tagging. Activities outside the selection are listed as
    excluded with the reason and are **not lost**:
    -   `effort_groups`: the observed geometric mean of the implied
        multipliers per label, with counts. It is descriptive and
        labelled "observed".
    -   `pace_vs_models`: each activity outside the selection against the
        fitted models, as predicted / observed (> 1 = faster than
        predicted). It is reported only for models that are fitted.
-   **No effort parameter in the models yet.** "Hard is x % faster" as a
    fitted factor needs hard and normal efforts on comparable terrain.
    Until then, the comparison above shows it without claiming it.

## Evidence (four activities, DGM1 elevation)

| Effort | Activities | Observed × DIN standard |
|---|---|---|
| hard (Activity A, Activity B) | 2 | ×2.08 |
| not stated (Activity C, Activity D) | 2 | ×1.58 |

**Effort and terrain are confounded in this data.** The two hard efforts
are also the steepest and longest climbs:

-   With the default selection, 2 routes remain, and their mix (`v / h`)
    varies by only ×1.24. The flat/vertical split (ADR-017) could not be
    separated even with enough routes.
-   ADR-017's preview ("vertical ×3.37") rested mostly on Activity A.
    Part of that is probably effort, not climbing ability.

## Consequences

-   **What helps most now:** ordinary-effort hikes that differ in
    steepness. Include at least one steep climb at a normal pace; only
    that separates "climbs fast" from "pushed hard".
-   With about 3 hard and 5 normal efforts on varied terrain, an explicit
    effort factor can become a model parameter (a later step).
-   Step 4 uses the effort selection when fitted assumptions feed
    `alpha scenarios`: plan with the normal baseline, and optionally show
    the hard pace as a separate scenario.
