# ALPHA --- Mountain Decision Intelligence

ALPHA analyses GPX planned routes and recorded hikes on your own
computer. It shows:

-   distance, ascent, gradient, and the steep sections of a route;
-   how a plan compares with hikes you have done;
-   how long a route may take, from assumptions you can see. These can
    be published standard speeds or speeds fitted from your own
    activities.

It explains its results in plain language, optionally reworded by a
local language model.

**Status:** Phases 0–8 done (analysis, storage, comparison, scenarios,
API and page, terrain models, personal speeds, explanations). Only
Phase 9 (map UI, deployment) remains, and it waits for a real need;
see [MVP_ROADMAP.md](MVP_ROADMAP.md).

ALPHA is not a navigation system, guide, or safety service. It never says
a route is safe or that you should go (see [Safety boundary](#safety-boundary)).

## Documentation

| Document | Read it for |
|---|---|
| [PROJECT_VISION.md](PROJECT_VISION.md) | Why ALPHA exists, user journeys, principles, scope |
| [DATA_MODEL.md](DATA_MODEL.md) | Every metric, formula, contract, report format, and table. **Authoritative for calculations.** |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Packages, dependency rules, data flows, API, security |
| [MVP_ROADMAP.md](MVP_ROADMAP.md) | What is done, what is open, phase exit criteria |
| [docs/adr/](docs/adr/README.md) | The reasoning and measurements behind each decision |
| [CLAUDE.md](CLAUDE.md) | Rules for AI coding assistants in this repository |

## Quick start

Requires Python 3.12+.

``` bash
python -m venv .venv
.venv\Scripts\activate          # Windows; Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"         # add ,explore for scripts/explore.py

alpha import my-hike.gpx        # store and analyse
alpha list                      # what is stored
alpha show 1                    # full analysis
alpha serve                     # the same in the browser: http://127.0.0.1:8000
```

Data lives in `data/local/` (git-ignored); use `--data-dir DIR` or
`ALPHA_DATA_DIR` for another place.

### Profiles and synthetic hikes

A profile is a separate database on this computer: no accounts, no
logins. `default` is your own data. A **synthetic** profile is for
generated test hikes. Its personal speeds are fitted on them and are
labelled `[synthetic data]` everywhere. Your own profile never uses
generated hikes.

``` bash
alpha profile create dev --synthetic
python scripts/synthetic_hikes.py --import-into dev             # variants of your hikes, known pace
python scripts/synthetic_hikes.py --invented --import-into dev  # or invented paths only
alpha --profile dev baseline                   # or set ALPHA_PROFILE=dev
alpha profile list
```

`alpha serve` has a profile switch in the page header. The generated
files and `manifest.json` (the true speeds) go to `data/local/synthetic/`.
Variants of your hikes contain your real coordinates, so they are never
committed. `python scripts/simulate_baseline.py` runs the simulation study
behind ADR-021.

## Using ALPHA

### Analyse

``` bash
alpha inspect FILE.gpx        # tracks, guessed kind and elevation source, data-quality warnings
alpha analyse FILE.gpx        # analysis without storing; --json for the full report
alpha import a.gpx b.gpx      # store; importing the same bytes again does nothing
alpha list                    # ID, kind (? = guessed), date, distance, ascent, moving time, DEM
alpha show 3 [--json]         # analysis of a stored track
alpha edit 1 --kind planned   # correct a guess (also --elevation-source)
alpha delete 2                # shows what would be deleted; add --yes to delete
```

Analysis options (`--profile-bin-m`, `--ascent-threshold-m`,
`--moving-min-speed-m-s`, …) change the assumptions. Every result states
the algorithm version and a hash of the options used, and results are
comparable only when both match.

### Compare a plan with what you did

``` bash
alpha edit 3 --planned-track 1   # activity 3 followed plan 1
alpha compare 1 3 [--json]       # 3 against 1: metrics, gradient bands, steepest climbs
```

### Estimate durations

``` bash
alpha scenarios 1 --break-min 30                                 # DIN 33466 standard hiker
alpha scenarios 1 --horizontal-speed-km-h 5 --ascent-rate-m-h 500  # your own speeds
alpha edit 2 --effort hard        # your judgement; personal speeds use normal efforts
alpha baseline                    # personal speeds fitted from your hikes
alpha scenarios 1 --personal --break-min 30   # estimate with your fitted speeds
```

`alpha baseline` fits:
-   one speed factor once you have 5 different routes;
-   separate flat and climbing speeds once you have 8 routes that differ
    in steepness.

Until then it says "not enough data" instead of guessing.
`--personal` fails rather than silently falling back to the standard
speeds.

### Terrain models (optional)

Your recorded elevation can be compared with a terrain model. ALPHA
reports the offset, the drift of a barometer, ascent from both series,
and how steep the ground around the path is.

**Copernicus GLO-30** (worldwide, 30 m, includes treetops) needs a free
OpenTopography API key. Copy `.env.example` to `.env` and fill in
`OPENTOPOGRAPHY_API_KEY`. Then:

``` bash
alpha enrich 3                 # download (fixed 0.25° tiles, never your track), sample, compare
alpha enrich --all             # every track without terrain data yet
alpha enrich 3 --offline       # never downloads; uses what is stored or cached
alpha show 3 --elevation dem:COP30   # any analysis on the terrain-model elevation instead
```

**Bavaria DGM1** (1 m, bare ground: the better reference in forest). On
geodaten.bayern.de/opengeodata select a region for DGM1 and save the
metalink (`.meta4`) in `data/local/`. Then:

``` bash
alpha get-tiles data/local/dgm1.meta4 --for-tracks        # plan: how many tiles, what the server learns
alpha get-tiles data/local/dgm1.meta4 --for-tracks --yes  # download
alpha enrich --all --dataset BY_DGM1                      # reads local tiles; nothing is sent
```

Without `--for-tracks` the whole region is downloaded, which can be
several GB. With it, only the tiles under your tracks are downloaded,
but the server learns which 1 km squares they cross. Tiles placed in
`data/local/dem/BY_DGM1/` by hand work too.

Attribution is printed with every result. For Copernicus: © DLR e.V. and
© Airbus Defence and Space GmbH, provided under COPERNICUS by the EU and
ESA. For DGM1: Bayerische Vermessungsverwaltung – www.geodaten.bayern.de,
CC BY 4.0.

### Explanations in plain language

``` bash
alpha explain scenarios 1 --personal   # templates: instant, exact, no model needed
alpha explain elevation 3 --dataset BY_DGM1
alpha explain baseline
alpha explain comparison 1 3           # track 3 against track 1
alpha explain baseline --llm           # reworded by a local model; every number checked
```

`--llm` needs [Ollama](https://ollama.com) running on this computer
(free; nothing leaves the machine):

``` bash
ollama pull llama3.1:8b    # about 5 GB; ALPHA's default model
```

A model answer is shown only if:
-   every number and duration matches ALPHA's report, with the right
    unit and next to the right words;
-   nothing is left out;
-   it avoids what ALPHA does not judge: safety, conditions, advice,
    certainty, links.

Otherwise you get the template text and the reason. `--language` is
English or German, the languages the checks understand.

On an RTX 3050 an explanation takes about 15–30 s. If Ollama's GPU
backend fails, ALPHA retries on the CPU (slower) and says so; updating
the graphics driver usually fixes this.

`python scripts/eval_explain.py --templates --attacks` measures the
checker against a catalogue of adversarial answers on your reports.
`--live` runs your model on all of them and through prompt-injection
probes.

### The local page and API

``` bash
alpha serve                          # http://127.0.0.1:8000; API docs at /docs
alpha serve --llm-model llama3.1:8b  # model behind "Reword with local model"
```

The page can:
-   import files, list and edit tracks, and link a hike to its plan;
-   draw the elevation and gradient profile;
-   compare two tracks;
-   estimate durations with standard or personal speeds;
-   show the terrain-model check, adding terrain data after a
    confirmation;
-   explain results, including a comparison.

It loads nothing from the internet. The API has no authentication, so
keep it on `127.0.0.1`.

## Development

``` bash
python -m ruff check .
python -m ruff format --check .
python -m mypy
python -m pytest
```

All four must pass; `.github/workflows/tests.yml` runs them on every
push. `tests/test_architecture.py` enforces the package
rules in [ARCHITECTURE.md](ARCHITECTURE.md). Tests never use the network.
Tests on your personal files run only when those files exist in
`data/local/`.

If an intended change alters results, bump `ALGORITHM_VERSION` and
refresh the golden outputs with
`python -m pytest tests/regression --update-golden`. They live in
`data/local/golden/`, so they are not in Git; check their diff before
you continue.

`python scripts/explore.py [file.gpx]` is the old exploration script, a
plotting sandbox whose numbers are not authoritative.

### Documentation site

The Markdown files in the repository root and `docs/adr/` are published
as a site on GitHub Pages. The files stay the single source; a script
collects them and rewrites their links.

``` bash
pip install -e ".[docs]"
python scripts/build_site_docs.py && mkdocs serve           # preview at http://127.0.0.1:8000
python scripts/build_site_docs.py && mkdocs build --strict  # fails on broken links
```

`.github/workflows/docs.yml` builds and deploys the site on every push
to `master` that changes the documentation. One-time setup: repository
**Settings → Pages → Source: GitHub Actions**.

The documentation names no personal hikes or places. Reference
activities are "Activity A–G" and "Plan 1–2". Tests find the real files
through `data/local/references.json` (git-ignored; format in
`data/references.example.json`).

## Data

-   **No real GPX file is shared.** Planned routes and activities, the
    database, terrain tiles, and the golden outputs derived from them all
    live in `data/local/`, which is git-ignored.
-   The only committed GPX files are the small synthetic fixtures in
    `tests/fixtures/`. `.gitignore` ignores every other `*.gpx`, so an
    export dropped anywhere in the repository stays private.
-   On a fresh clone the tests that need your files are skipped; the
    synthetic fixtures cover the same code.

## Safety boundary

ALPHA is an experimental analysis tool. It is not a navigation system,
guide, avalanche or weather service, or safety certification. It never
says a route is safe or recommends going. Use current official
forecasts, avalanche bulletins, route reports, maps, and qualified local
advice.
