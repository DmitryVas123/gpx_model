# ALPHA --- Mountain Decision Intelligence

**Language:** Python\
**Status:** Phase 3 complete (local storage and comparison). Next: the
duration scenario engine (Phase 4); see [MVP_ROADMAP.md](MVP_ROADMAP.md).

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
alpha delete 2                # shows what would be deleted; add --yes to delete
```

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
