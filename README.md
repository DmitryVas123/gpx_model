# ALPHA --- Mountain Decision Intelligence

**Language:** Python\
**Status:** MVP complete (Phases 0–5): CLI, local HTTP API, and web page.
Next: post-MVP phases; see [MVP_ROADMAP.md](MVP_ROADMAP.md).

ALPHA analyses GPX planned routes and recorded activities. It
calculates distance, ascent, gradient and climb sections, compares a
planned route with past activities, and estimates durations from
assumptions the user sets explicitly.

## Documentation

-   [Project Vision](PROJECT_VISION.md): problem, user, journeys,
    principles, scope.
-   [Data Model](DATA_MODEL.md): glossary, metric definitions,
    contracts, persistence, data quality. Start here for any
    calculation.
-   [Architecture](ARCHITECTURE.md): modules, dependency rules, source
    tree by phase, ADR list.
-   [MVP and Roadmap](MVP_ROADMAP.md): current state, phases, exit
    criteria, first backlog.
-   [CLAUDE.md](CLAUDE.md): rules for AI coding assistants working in
    this repository.

## Development

Requires Python 3.12+.

``` bash
python -m venv .venv
.venv\Scripts\activate            # Windows; on Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"           # add ,explore for scripts/explore.py

ruff check . && ruff format --check . && mypy && pytest
```

### Command line

Single files, nothing stored:

``` bash
alpha inspect FILE.gpx        # tracks, inferred kind/elevation source, data-quality warnings
alpha analyse FILE.gpx        # distance, ascent, time, gradient distribution, climb sections
alpha analyse FILE.gpx --json # full versioned report incl. 50 m profile (DATA_MODEL.md §6.1)
alpha analyse FILE.gpx --moving-min-speed-m-s 0.25 --profile-bin-m 25   # change assumptions
```

Stored tracks (database and original copies in `data/local/`, or
`--data-dir DIR` / `ALPHA_DATA_DIR`):

``` bash
alpha import a.gpx b.gpx      # store and analyse; re-importing identical bytes is a no-op
alpha list                    # ID, kind (? = guessed), date, distance, ascent, moving time
alpha show 3 [--json]         # full analysis of a stored track (same options as analyse)
alpha compare 1 3 [--json]    # track 3 against track 1: metrics, gradient bands, climbs, notes
alpha edit 3 --planned-track 1             # link an activity to the plan it followed
alpha edit 1 --kind planned --elevation-source planner_dem   # confirm or correct guesses
alpha scenarios 1 --break-min 30          # DIN 33466 estimate range + observed times
alpha scenarios 1 --horizontal-speed-km-h 5 --ascent-rate-m-h 500   # your own assumptions
alpha enrich 3                # DEM elevation (opt-in download) vs recorded elevation
alpha enrich --all            # every track that has no DEM elevation yet
alpha show 3 --elevation dem:COP30         # any analysis on the DEM series instead
alpha compare 1 3 --elevation dem:COP30    # (both tracks need `alpha enrich` first)
alpha delete 2                # shows what would be deleted; add --yes to delete
```

### DEM enrichment (optional)

The elevation check compares your recorded elevation with a terrain
model: offset, spread, **drift** (warning `ELEVATION_DRIFT`), ascent from
both, and **terrain slope** (steepness of the ground around the track in
10° bands; not the track gradient and not a hazard rating).

`alpha enrich ID` needs an OpenTopography API key: copy `.env.example` to
`.env` and fill in `OPENTOPOGRAPHY_API_KEY`. Only fixed 0.25° tiles are
requested (never your track); they are cached in `data/local/dem/`, and
`--offline` never downloads. Copernicus DEM attribution is printed with
every result (ADR-012). `alpha list` shows which tracks have DEM
elevation. With the key in `.env`, `alpha serve` also lets the page add
DEM elevation (after a confirmation); without it, the page uses only
what is already stored.

**Bavaria DGM1 (1 m terrain model, no trees):** on
geodaten.bayern.de/opengeodata select a region for DGM1 and save the
metalink (`.meta4`) in `data/local/`, then:

``` bash
alpha get-tiles data/local/dgm1.meta4 --for-tracks   # plan only: what, how many, privacy
alpha get-tiles data/local/dgm1.meta4 --for-tracks --yes
alpha enrich --all --dataset BY_DGM1      # reads local tiles; nothing is sent
alpha show 3 --elevation dem:BY_DGM1
```

Without `--for-tracks` all tiles of the region are downloaded (can be
several GB). With it, only tiles under your stored tracks, but the server
then learns which 1 km squares they cross. Tiles you place in
`data/local/dem/BY_DGM1/` by hand work too.

Data: Bayerische Vermessungsverwaltung – www.geodaten.bayern.de,
CC BY 4.0 (ADR-013).

### Local web page and API

``` bash
alpha serve                   # http://127.0.0.1:8000  (page at /, API docs at /docs)
```

The page imports files, lists tracks, draws the elevation and gradient
profile, edits and links tracks, compares two tracks, and shows duration
scenarios. It works offline and loads nothing from the internet. The API
has no authentication, so keep it on `127.0.0.1` (ADR-011).

Every result states the algorithm version and a hash of the parameters
used; results are comparable only when both match. Metric definitions
are in [DATA_MODEL.md](DATA_MODEL.md) §5 and the reasoning behind the
defaults in `docs/adr/`.

If an intended change alters results, bump `ALGORITHM_VERSION` and
refresh the golden outputs with `pytest tests/regression --update-golden`.

`python scripts/explore.py [file.gpx]` runs the old exploration script
(plotting sandbox; its numbers are not authoritative).

## Data

-   `data/samples/`: shareable GPX (synthetic or planned routes).
-   `data/local/`: personal activities and the local database;
    git-ignored. Never commit recorded activities.
-   `.gitignore` ignores every `*.gpx` outside `data/samples/` and
    `tests/fixtures/`, so a new export dropped anywhere else stays
    private.

## First milestone

1.  Read one GPX file (tracks and routes) into a typed `Track`.
2.  Report data-quality warnings.
3.  Calculate distance, ascent/descent, profile-bin gradient, climb
    sections, and activity time metrics.
4.  Return a versioned `Analysis`.
5.  Test against small hand-checked fixtures and golden outputs for the
    reference files.

## Safety boundary

ALPHA is an experimental analysis tool, not a navigation system, guide,
avalanche forecast, weather service, or safety certification. It never
says a route is safe or recommends going. Use current official
forecasts, avalanche bulletins, route reports, maps, and qualified local
advice.
