# ADR-021: Local profiles, synthetic hikes, and a simulation study

**Status:** Accepted
**Date:** 2026-11-27
**Code:** `alpha.application.profiles`, `alpha.application.baseline`,
`alpha.api` (context, meta router, page), `alpha.cli`,
`scripts/synthetic_hikes.py`, `scripts/simulate_baseline.py`

## Context

The personal speed models (ADR-016, ADR-017) need more routes than the
owner has recorded: model 1 is fitted on 5 routes, and model 2 needs 8.
More recordings will come slowly. Meanwhile there are open questions:

-   Are the gates right?
-   Do the intervals mean what they say?
-   Which kind of route would help most?

Real data cannot answer them, because nobody knows the true speeds of a
real hiker. Generated data can, but it must never leak into the real
model or be mistaken for real results.

The owner also wanted a separate development data set, kept apart from
their own data, that can be switched to in the page.

## Decision

**Profiles.** A profile is a separate data folder: its own `alpha.db`
and `files/`.

-   `default` is the data directory itself, so existing data stays where
    it is.
-   Other profiles live in `<data directory>/profiles/<name>/` with a
    `profile.json`.
-   Terrain tiles stay shared in `<data directory>/dem/`. They are public
    data and can be several GB.
-   There are no accounts or logins. ALPHA stays single-user and local
    (ADR-011); a profile only keeps data sets apart.
-   `--profile NAME` / `ALPHA_PROFILE` select one in the CLI.
    `alpha profile create NAME [--synthetic]` and `alpha profile list`
    manage them.
-   An unknown name is an error, never a new empty folder. A typo must
    not look like lost data.
-   The page switches the server's active profile (`PUT /profile`) and
    shows a banner in a synthetic profile. The API reports profile names
    and flags only, never folder paths.

**Synthetic data is labelled at every step.**

-   Generated hikes have the GPX activity type `synthetic-hiking`.
    Personal profiles exclude that type from fits, with the usual
    exclusion reason.
-   Only a profile created with `--synthetic` adds that type to its fits.
    The generator refuses to import into any other profile.
-   A fit that used a synthetic hike says so in every output:
    -   `synthetic_data` and `synthetic_track_ids` in the baseline report;
    -   `personal.synthetic_data` and a note in scenario reports;
    -   `[synthetic data]` in the assumptions' label;
    -   a caveat in explanations, which is never sent to a model.

**Generator** (`scripts/synthetic_hikes.py`). The hiker's speeds are
known: DIN 33466 with a flat factor `kh` and a climbing factor `kv`.

-   **Pace:** a lognormal day-to-day factor; local pace changes; DIN's
    rule applied per step and scaled so the whole hike follows DIN.
-   **Stops:** 2–4 stops of 2–15 min.
-   **Noise:** GPS noise of ~3 m correlated over ~5 min; barometer noise
    of ~0.3 m.
-   **Effort:** a share of "hard" efforts (pace ×1.15), annotated so
    that fits on normal efforts can leave them out.
-   **Paths:**
    -   **Variants of the owner's activities:** original, reversed,
        stretched, steeper, flatter, and one-way up. The source path is
        first smoothed over 30 m, so its own GPS jitter does not count as
        distance.
    -   **Invented loops**, with no personal data.
-   **Labels:** names are neutral ("Synthetic S07 (steeper)") and dates
    are invented (2030 on). `manifest.json` records the true values.
-   **Storage:** variants of real paths contain real coordinates, so
    files stay in `data/local/synthetic/` (git-ignored).

**Simulation study** (`scripts/simulate_baseline.py`). The study fits
models 1 and 2 on plain samples, not GPX, which takes seconds per
thousand fits.

-   The route shapes are the owner's 5 fitted routes, plus variants.
-   The day-to-day spread is the owner's: σ = 0.087 in ln moving time.
-   Each table row has 200 simulated hikers.

## Evidence

### Simulation (plain samples, 200 hikers per row)

Hiker A has one factor (kh = kv = 1.45). Hiker B climbs relatively
better (kh 1.25, kv 1.70). "Typical/steep/flat" is the median error of
the predicted moving time on new routes of that kind, using the model
that a scenario would choose (the one with the smaller leave-one-route-out
error).

| Routes | Mix | B: kh / kv error (median) | B: kh / kv 95 % coverage | B: typical / steep / flat error |
|---|---|---|---|---|
| 5 | similar | (model 2 not fitted) | – | 5.3 / **15.1** / 7.3 % |
| 8 | similar | 3.3 / 8.2 % | 94 / 91 % | 3.5 / 7.9 / 4.1 % |
| 8 | +2 steep | 3.3 / 5.5 % | 96 / 91 % | 3.2 / **4.8** / 4.0 % |
| 8 | +2 flat | 3.0 / 10.9 % | 94 / 90 % | 3.4 / 8.2 / 2.8 % |
| 8 | +2 one-way up | 3.3 / 8.2 % | 93 / 91 % | 3.6 / 8.0 / 4.3 % |
| 12 | similar | 2.6 / 7.2 % | 93 / 91 % | 2.6 / 6.2 / 3.6 % |
| 12 | +2 steep | 2.7 / 5.1 % | 95 / 92 % | 2.3 / 3.9 / 2.8 % |
| 20 | similar | 2.4 / 6.5 % | 94 / 91 % | 2.0 / 4.9 / 2.8 % |

Findings:

-   **The 8-route gate is adequate.** With the owner's route shapes,
    model 2 was fitted in every run at 8 routes. Its intervals covered
    the truth in 90–96 % of runs, against a nominal 95 %; the climbing
    factor was slightly below. No run was "not identifiable".
-   **Model 1 misleads on steep routes if the hiker is not uniform.**
    At 5 routes, hiker B's steep routes were mispredicted by 15 %, while
    typical routes were off by only 5 %. Model 2 at 8 routes halves this.
-   **Steep routes help most.** Two steep routes among 8 cut the error
    on steep routes from 7.9 % to 4.8 %, the same as 12 more similar
    routes would. Flat routes and one-way ascents did not help the
    climbing factor. The owner's routes are dominated by horizontal time
    (DIN vertical/horizontal ratio 0.02–1.19), so the climbing factor is
    the poorly known one.
-   **Model 2 costs nothing for a uniform hiker.** For hiker A, the
    chosen model's errors stayed at 2.2–2.6 % at 8 routes, as with
    model 1. Model 1's intervals covered the truth in 94–96 % of runs.
-   **Unannotated hard efforts bias the factor mildly.** With 10 / 20 /
    30 % of hikes at ×1.15 pace but unannotated, model 1's factor rose
    by 2.0 / 3.1 / 4.6 %. That is less than the single-hike spread, but
    it is systematic, so annotation (ADR-018) remains worthwhile.

### Generated GPX through the whole pipeline

These hikes were generated from paths, imported, and analysed, then
compared with the generator's truth.

-   **Invented loops (≤ 18 % mean gradient):** ALPHA's distance is
    0.9–2.0 % above the generated path (GPS noise), ascent within 1.2 %,
    and moving time within 0.4 %. Stops were removed correctly.
-   **Variants of the owner's 7 activities (41 hikes):** distance 0–3 %
    high and ascent mostly within 5 %. The near-flat Activity G gives
    large ratios on a few metres of ascent.
-   **Moving time on steep ground was up to 31 % short** (median 3 %).
    The simulated hiker climbed the steepest parts at under 0.3 m/s,
    which ADR-007 counts as stopped: up to 27 % of the time on the
    steepest variant.
-   **Consequence for the fit:** generated with kh 1.30 and kv 1.60, the
    dev profile's model 2 fitted flat 1.29 (95 %: 1.25–1.34) but
    vertical **1.88 (1.77–1.99)**. The truth lies outside the interval.
    The lost minutes all come from climbs, so climbing looks faster.
    With the moving threshold lowered for the fit (`--moving-min-speed-m-s`):

| Threshold | Vertical factor (truth 1.60) | Flat factor (truth 1.30) |
|---|---|---|
| 0.30 m/s (default) | 1.88 (1.77–1.99) | 1.29 |
| 0.20 m/s | 1.67 (1.59–1.75) | 1.31 |
| 0.15 m/s | 1.63 (1.55–1.71) | 1.30 |

## Consequences

-   The gates (5 and 8 routes, ratio range ×1.5) stay. The study gives
    no reason to change them for routes like the owner's.
-   The advice for new recordings becomes concrete: **steep routes at
    normal effort** are the most useful, ahead of more of the same.
-   **Open question:** the default moving threshold of 0.3 m/s comes
    from matching Strava's moving time (ADR-007), and it is right for
    that purpose. For speed fits it may count slow steep climbing as
    stopped and inflate the climbing factor. Whether this happens on
    the owner's real steep hikes (Activities A and B) needs a check of
    how much of their climbing time lies between 0.15 and 0.3 m/s. If
    it is material, the fit should use its own threshold, recorded
    with the fit. This is not changed yet: the effect is measured only
    on generated hikes, whose pace on steep ground is an assumption.
-   The generated hikes test the pipeline and the models. They do not
    test whether DIN 33466 describes a real person; only real
    recordings can.
-   A real person's profile can never contain fitted synthetic data.
    Synthetic results are labelled in every output that reports a fit.
