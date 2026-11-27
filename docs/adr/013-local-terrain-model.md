# ADR-013: Local terrain model tiles (Bavaria DGM1)

-   **Status:** Accepted (Phase 6)
-   **Date:** 2026-11-22
-   **Code:** `alpha.dem.local`, `alpha.dem.metalink`,
    `alpha.dem.datasets` (`BY_DGM1`), `alpha.dem.open_dem`, `alpha get-tiles`

## Context

COP30 (ADR-012) is a **surface** model: in forest it follows the
treetops, which can add gain. A **terrain** model (bare earth) is needed to
measure that effect. The Bavarian survey office publishes a 1 m terrain
model (DGM1) as open data, as:

| Option | Assessment |
|---|---|
| REST height service (`geoservices.bayern.de/bvvapi/od/hoehen`, single point or list of points) | **Rejected.** Every request contains the track's coordinates, so the provider would receive the route itself, which ADR-012 rules out. The underlying model, resolution, and limits are not documented on the service page, and the linked interface description returned 404 (checked 2026-11-22). |
| ASCII XYZ | **Rejected.** The same grid as text: several times larger and slower to read, with no extra information. |
| **GeoTIFF tiles** | **Chosen.** Read with rasterio like COP30. The portal delivers a region only as a **metalink** (`.meta4`, RFC 5854): an XML list of 1 km tiles named `<km east>_<km north>.tif`, each with two mirror URLs (`download1/2.bayernwolke.de`), with no sizes or checksums. |

## Decision

-   **Dataset `BY_DGM1`:**
    -   Terrain model (`surface="terrain"`), 1 m.
    -   Heights DHHN2016, as published for Bavarian height products. It
        is not read from the files, and it is deliberately left
        unconfirmed: the use is private and non-commercial, and ALPHA
        compares offsets and ascent, not absolute heights.
    -   Licence CC BY 4.0. Attribution (from the open-data terms):
        "Datenquelle: Bayerische Vermessungsverwaltung –
        www.geodaten.bayern.de (CC BY 4.0)". Shown with every result.
-   **Reading is local:** tiles live in `<data dir>/dem/BY_DGM1/`
    (subfolders allowed, git-ignored with `data/local/`). Sampling never
    uses the network, and no API key is involved.
-   **Downloading from the metalink, only when asked:**
    `alpha get-tiles FILE.meta4 [--for-tracks] [--yes]`.
    -   Without `--yes` it only prints the plan: tiles listed, selected,
        already present, hosts, and what the server learns.
    -   All tiles (default): the server learns only the region the user
        chose on the portal, but the download can be several GB (770
        tiles for one district).
    -   `--for-tracks`: only tiles under stored tracks, plus the
        neighbour where a point is within 2 m of a tile edge (needed at
        seams). Much smaller (15 tiles for the two reference activities),
        but the server learns which 1 km squares the tracks cross: the
        route at 1 km resolution. The user chooses; there is no silent
        default.
    -   The metalink is untrusted: tile names must be plain file names
        (no paths); only HTTPS URLs on the dataset's known hosts are used;
        the file is limited to 20 MB and each tile to 256 MB.
    -   Downloads stream to a `.part` file and are renamed when complete.
        The second mirror is tried if the first fails. The first tile no
        mirror delivers stops the batch. Tiles already present are
        skipped.
    -   Selection relies on the name meaning the **lower-left corner** in
        EPSG:25832. This is an assumption, so every downloaded tile is
        checked: it must be a north-up GeoTIFF in EPSG:25832 whose
        footprint equals the 1 km square of its name (±1 m). Otherwise it
        is deleted and the download stops.
-   **When sampling, nothing about the tiles is assumed:** CRS, bounds,
    pixel size, and no-data value are read from each file. Tile names
    are used only to select downloads (above). All
    tiles of a dataset must share one CRS; mixed CRS is an error.
-   **Index:** `_index.json` in the dataset folder stores each file's
    footprint, size, and modification time. A file is opened again only
    when it changes; an unreadable index is rebuilt.
-   **Sampling:**
    -   Track points are transformed from WGS84 to the tiles' CRS with
        pyproj.
    -   Each point belongs to the first tile containing it.
    -   The tile is read with a one-pixel ring from its neighbours
        (`rasterio.merge`), so points near a seam are interpolated
        across it instead of becoming NaN.
    -   Bilinear sampling, knots every 20 m, and storage are unchanged from
        ADR-012. With the same spacing, COP30 and DGM1 ascents can be
        compared.
-   **Coverage:**
    -   Points outside every tile are NaN, never extrapolated.
    -   A track with no covered point is an error (`DemUnavailableError`).
    -   A partly covered track is stored with its coverage reported. For
        example, the part of a border route outside Bavaria has no data.
-   **One sampler interface:** `ElevationSampler` (a Protocol) with
    `dataset` and `sample()`. It has two implementations, the
    OpenTopography `DemProvider` and `LocalTileProvider`. `open_dem()`
    picks the right one; the application layer only sees the Protocol.
-   **Entry points:**
    -   CLI: `alpha get-tiles`, then `alpha enrich ID --dataset BY_DGM1`
        (and `--all`).
    -   API: `POST /tracks/{id}/dem?dataset=BY_DGM1`.
    -   Page: a "Sample from local tiles" button. It needs no confirmation
        because nothing leaves the computer.
    -   `GET /dem/datasets` reports `source` (`opentopography` or `local`)
        and where to obtain local tiles.

## Evidence (reference activities, 2026-11-22)

`alpha get-tiles --for-tracks` fetched 15 tiles. Every tile passed the
footprint check, so the lower-left naming assumption holds.

**Ascent / descent, 3 m hysteresis, by DEM sample spacing:**

| Track | Recorded | Strava | Spacing | COP30 (surface) | DGM1 (terrain) |
|---|---|---|---|---|---|
| Activity B | 1192 / 1200 | 1309 | 10 m | 1384 / 1396 | 1248 / 1257 |
| | | | **20 m** | **1311 / 1324** | **1239 / 1247** |
| | | | 30 m | 1276 / 1288 | 1209 / 1218 |
| | | | 50 m | 1232 / 1244 | 1205 / 1213 |
| | | | 100 m | 1199 / 1212 | 1195 / 1203 |
| Activity A | 1377 / 18 | 1443 | 10 m | 1484 / 99 | 1462 / 74 |
| | | | **20 m** | **1441 / 56** | **1436 / 47** |
| | | | 30 m | 1422 / 38 | 1433 / 45 |
| | | | 50 m | 1418 / 34 | 1409 / 21 |
| | | | 100 m | 1384 / 0 | 1388 / 0 |

**Two more activities (20 m spacing):**

| Track | Recorded | COP30 | DGM1 | COP30 − DGM1 |
|---|---|---|---|---|
| Activity C | 554 / 570 | 569 | 578 / 593 | −9 m |
| Activity D (evening forest walk) | 814 / 819 | **1080** | **845 / 853** | **+235 m (+28 %)** |

The canopy effect ranges from none (Activity C, Activity A) to +28 %
(Activity D). It depends on how much of the route is under trees, so a
fixed correction factor is not possible. On Activity D the recorded
barometric series is close to the terrain model (−31 m), and COP30 is far
off.

**COP30 minus DGM1 along the track (20 m):**

| Track | Median | 95th percentile | Min / max | Share of distance > 5 m |
|---|---|---|---|---|
| Activity B | +1.6 m | +12.7 m | −5 / +30 m | 22 % |
| Activity A | 0.0 m | +24.9 m | −39 / +47 m | 24 % |

**Findings:**

-   **The forest effect is real, but it depends on the route.**
    -   Activity B: COP30 is 72 m (+5.8 %) above DGM1. COP30 gain also falls
        steadily with wider spacing (1384 → 1199 m), the signature of
        canopy roughness, while DGM1 stays within 1205–1248 m from 10 to
        50 m. COP30 lies only up to 5 m below DGM1 (canopy and buildings
        only raise a surface model), so the excess is canopy.
    -   Activity A: COP30 and DGM1 differ by only 5 m. Most of the
        route is above the treeline. The large differences of both signs
        (−39 to +47 m) are steep terrain that a 30 m model smooths,
        not trees.
    -   So "COP30 > DGM1 by 5 m" indicates forest only in moderate
        terrain. It is not a general canopy detector.
-   **Strava's gain includes the canopy effect.** Strava (1309 / 1443 m)
    matches COP30 at 20 m (1311 / 1441 m, ADR-012). On the forested route
    it is about 70 m above the terrain model.
-   **DGM1 is the best terrain reference, but not exact.** On steep
    ground its gain still depends on spacing (Activity A: 1462 m at 10 m,
    1409 m at 50 m). There, GPS position error picks up real micro-relief
    such as rocks, path cuts, and switchbacks. 20 m remains the convention
    (ADR-012) so that series stay comparable; treat ±2–3 % as the spread
    between reasonable choices.
-   **Recorded (barometric) elevation has its own errors, and the terrain
    models expose them.**
    -   Activity A: from about km 8.1 to the end, recorded elevation is
        about 40 m below both DEMs (2175 m vs 2212–2219 m), while the
        overall median offset is ≈ 0. The barometer drifted during the
        ascent, which alone explains most of the 1377 vs 1436 m
        difference.
    -   Activity B: a short excursion at km 6.8–6.9, up to 25 m above both
        DEMs, which agree with each other there.

## Consequences

-   For Bavarian routes, `--elevation dem:BY_DGM1` is the most faithful
    ascent. COP30 is fine above the treeline but overstates gain in
    forest: by 6 % on Activity B and 28 % on Activity D.
-   Follow-up done: `ELEVATION_DRIFT`, a check for drift of recorded
    elevation against the reference along the track (ADR-014).
-   Other national terrain models in GeoTIFF (e.g. Austria's) only need a
    `DATASETS` entry with their attribution.
-   With 1 m pixels, sampling at 20 m knots matters even more: GPS
    position error on a slope would otherwise show up as height noise
    (ADR-012).
