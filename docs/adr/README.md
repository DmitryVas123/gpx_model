# Architecture decision records

One decision per file: context, decision, evidence, consequences. An ADR
is changed only to record a revision or a new finding (dated in the
text); a reversed decision gets a new ADR that supersedes it.

| ADR | Decision | Phase | Status |
|---|---|---|---|
| [001](001-modular-monolith.md) | Modular monolith; layers introduced when needed | 0 | Accepted |
| [002](002-sqlite-storage.md) | SQLite, original files by SHA-256, cached analyses, additive schema upgrades | 3 | Accepted |
| [004](004-ascent-hysteresis.md) | Ascent and descent by 3 m hysteresis for every elevation source | 2 | Accepted |
| [005](005-scenario-methods-and-assumptions.md) | Scenario methods (formulas) vs assumptions with a source; ML outside the domain | 4 | Accepted |
| [007](007-moving-time-and-stops.md) | Moving time from 3D windowed speed (Strava-calibrated) | 2 | Accepted, rev. 2 |
| [008](008-column-oriented-track.md) | Column-oriented `Track` with NumPy arrays | 0 | Accepted |
| [009](009-profile-bins-and-gradient.md) | 50 m profile bins, unclipped gradient, gradient bands, climb sections | 2 | Accepted |
| [010](010-distance-jitter-filter.md) | Distance with a 3 m minimum-displacement filter | 2 | Accepted |
| [011](011-http-api-and-local-ui.md) | HTTP API, error format, request log, local page; no background workers | 5 | Accepted |
| [012](012-dem-enrichment.md) | DEM enrichment from Copernicus GLO-30 (fixed tiles, 20 m knots, no fallback) | 6 | Accepted |
| [013](013-local-terrain-model.md) | Local terrain model tiles (Bavaria DGM1), metalink downloads | 6 | Accepted |
| [014](014-elevation-drift-check.md) | Drift of recorded elevation against a reference model | 6 | Accepted |
| [015](015-terrain-slope.md) | Terrain slope from the elevation model, separate from track gradient | 6 | Accepted |
| [016](016-personal-speed-baseline.md) | Personal speed multiplier, route-grouped validation, 5-route gate | 7 | Accepted |
| [017](017-flat-and-vertical-factors.md) | Separate flat and vertical speed factors, 8-route gate | 7 | Accepted |
| [018](018-effort-annotation.md) | Effort annotation by the user; baselines fit normal efforts | 7 | Accepted |
| [019](019-personal-assumptions-in-scenarios.md) | Fitted speeds in scenarios, own variation as the range, no fallback | 7 | Accepted |
| [020](020-explanations-and-local-llm.md) | Template explanations; a local model may reword them, validated | 8 | Accepted |

Numbers not in the table were reserved during planning and never
written:

-   003: GPX as the only input format. This stayed true without needing
    a record.
-   006: background workers. Rejected in ADR-011.

## Writing a new ADR

-   Next free number, file `NNN-short-title.md`.
-   Header lines: **Status**, **Date**, **Code** (modules affected).
-   Sections: Context, Decision, Evidence (measurements, if any),
    Consequences.
-   Add a row to the table above and a line to the `Decisions` section
    of `mkdocs.yml`; `scripts/build_site_docs.py` fails if an ADR is
    missing there.
-   Link the ADR from DATA_MODEL.md when it defines or changes a metric,
    contract, or default.
-   Describe evidence from personal data with neutral labels (Activity A,
    Plan 1); never name hikes, places, or dates.
