# ADR-012: DEM enrichment via OpenTopography

-   **Status:** Accepted (Phase 6; completed by ADR-013 to ADR-015)
-   **Date:** 2026-11-22
-   **Code:** `alpha.dem`, `alpha.domain.reference_elevation`,
    `alpha.application.elevation`, `alpha enrich`

## Context

Track elevation comes from a barometer, GPS, or a route planner's own
terrain model. Each has its own errors, and ascent depends strongly on
them. Strava reported more gain than the exported elevation can produce
(ADR-004). A DEM gives an independent reference series to compare
against.

## Decision

-   **Dataset:** Copernicus DEM GLO-30 (`COP30`), fetched through the
    OpenTopography global DEM API.
    -   ~30 m resolution.
    -   A **surface** model: it can show treetops or roofs.
    -   Heights relative to EGM2008.
    -   Its attribution is stored with every profile and printed with
        every result.
    -   The registry (`alpha.dem.datasets`) can take more datasets, such
        as a national ground model.
-   **Opt-in only:** nothing is downloaded unless the user runs
    `alpha enrich`. `--offline` never downloads. The API key is read from
    `OPENTOPOGRAPHY_API_KEY` (environment or `.env`). It is never printed,
    logged, stored, or included in error messages (tested).
-   **Privacy-preserving requests:** tiles come from a **fixed 0.25° grid**
    with a 0.002° margin, never from the track's bounding box. The provider
    learns only which grid cells are of interest, not the route's outline.
    Neighbouring tracks reuse cached tiles.
-   **Local cache:** `<data dir>/dem/<dataset>/<tile>.tif` plus a JSON
    sidecar with dataset, bounds, and retrieval time. Git-ignored with
    `data/local/`.
-   **Sampling:** bilinear interpolation between pixel centres. NaN
    outside coverage or next to no-data; nothing is invented. GDAL reports
    the tiles' `PixelIsPoint` georeferencing with the usual corner-based
    transform, which the sampler expects; median recorded-minus-DEM
    offsets near 0 m confirm the alignment.
-   **Sample spacing 20 m along the track, not at every point.** At 1 Hz,
    GPS lateral jitter on a slope becomes vertical noise in the DEM value:

    | Track | DEM gain at every point (threshold 0 / 3 m) | DEM gain at 20 m spacing (3 m) | Strava |
    |---|---|---|---|
    | Activity A | 1862 / 1531 m | **1441 m** | 1443 m |
    | Activity B | 2224 / 1481 m | **1311 m** | 1309 m |
    | Plan 1 (planner, 9 m spacing) | 1458 / 1444 m | 1404 m | — |

    The DEM is sampled at *knots*: per source segment, the first point,
    every point of the displacement filter (ADR-010) at 20 m, and the last
    point. Values in between are linearly interpolated along the track.
    This gives one smooth value per point, so the rest of the analysis can
    use the series unchanged. 20 m is about 2/3 of the 30 m pixel; denser
    sampling adds no terrain information, only jitter.
-   **Storage:** `dem_profiles` (one row per track and dataset) holds the
    per-point series as float64 bytes plus provenance: dataset, name,
    resolution, surface/terrain, vertical datum, attribution, spacing,
    tiles, retrieved/created time. The recorded `ele_raw_m` is never
    changed.
-   **Comparison** (`compare_elevation`, pure domain), recorded minus
    reference:
    -   median (the robust offset);
    -   mean and standard deviation (shape disagreement once the offset is
        removed);
    -   95th-percentile and maximum absolute difference, with the latter's
        position along the track;
    -   ascent and descent from both series with the same hysteresis
        threshold.
-   **Schema 2** adds the table through an **additive upgrade**: an
    existing version-1 database gains the new table in place, with no
    re-import (ADR-002 amended).
-   `rasterio` and any network library are imported only in `alpha.dem`
    (enforced by `tests/test_architecture.py`). Tests use synthetic
    GeoTIFFs and never touch the network.

## Evidence (reference tracks, COP30)

| Track | Recorded − DEM median | Spread (std) | 95 % within | Recorded gain | DEM gain |
|---|---|---|---|---|---|
| Plan 1 (mapy) | −0.7 m | 7.1 m | ±16 m | 1368 m | 1404 m |
| Plan 2 (mapy) | ≈ −7 m | ≈ 6 m | ±18 m | 1301 m | 1331 m |
| Activity A (Strava) | −0.1 m | 12.5 m | ±35 m | 1377 m | 1441 m |
| Activity B (Strava) | −0.4 m | 5.1 m | ±12 m | 1192 m | 1311 m |

## Consequences

-   Strava's reported gain is explained: it matches COP30-like DEM
    elevation sampled at about 20 m (ADR-004).
-   DEM gain is not ground truth either. A surface model adds treetop
    bumps in forest, and the recorded barometric series has its own drift.
    Both are shown side by side, labelled by source.
-   **Done (algorithm 0.3.0):** the analysis can use the DEM series via
    `--elevation dem:COP30` / `?elevation=dem:COP30` (part of the params
    hash).
    -   Every elevation-dependent value follows the chosen series. Moving
        time changes < 0.5 min, so time is not treated separately.
    -   Outputs carry `elevation_series` with the attribution.
    -   Recorded-elevation results are numerically unchanged; golden files
        changed only in version, hash, and the new fields.
-   **Done (report format 4):** enrichment outside a single CLI call.
    -   `alpha enrich --all` enriches every track without a profile for
        the dataset (`--refresh`: all). A track whose tiles are missing
        while downloads are off is reported and the next one is tried; a
        failed download stops the batch so a key or network problem is
        not repeated per track.
    -   `POST /tracks/{id}/dem[?dataset=&refresh=&offline=]` and
        `GET /tracks/{id}/dem/{dataset}` (stored profile only, no network).
        `GET /dem/datasets` lists datasets and `downloads_available`.
    -   The API key reaches the server only as an argument from
        `alpha serve`; it is kept in memory and never returned or logged.
        Without it the server uses cached tiles only (503
        `DEM_UNAVAILABLE`); a refused or failed download is 502
        `DEM_DOWNLOAD_FAILED`. Enrichments are serialised (shared tile
        cache).
    -   The page shows the elevation check per track and offers
        downloading only after an explicit confirmation that states what
        is sent.
-   **Phase 6 completed by** ADR-013 (local terrain model, Bavaria DGM1),
    ADR-014 (drift check), and ADR-015 (terrain slope; tile margins are
    reused, so probes and tracks near a tile edge cause no extra
    downloads).
