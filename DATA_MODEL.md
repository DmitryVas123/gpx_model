# ALPHA --- Data Model and Contracts

## 1. Modelling principles

-   Store the original GPX bytes unchanged; everything else is derived
    from them and can be rebuilt.
-   Keep units explicit in field names (`distance_m`, `duration_s`,
    `gradient_percent`).
-   Preserve provenance: every value is **observed**, **calculated**,
    **estimated**, or **unknown** (see §9).
-   Distinguish missing values from zero (`None`, never `0`).
-   Version calculations **and** their parameters so results can be
    reproduced and compared.
-   Timestamps are UTC internally. GPX `<time>` is UTC by specification;
    local time zone is unknown unless derived later.
-   Do not persist derived values that are cheap to recompute unless
    there is a measured reason.

## 2. Glossary

The word "segment" is deliberately **not** used on its own because it
is ambiguous.

| Term | Meaning |
|---|---|
| **Source file** | One imported GPX file, identified by SHA-256 of its bytes. |
| **Track** | One geometry extracted from a source file: a GPX `<trk>` or `<rte>`. A file can yield several tracks. |
| **Source segment** | A GPX `<trkseg>`. Boundaries usually mean a recording gap; distance is **never** summed across them. |
| **Step** | The interval between two consecutive points in the same source segment. Internal calculation unit; too noisy to report directly. |
| **Profile bin** | A fixed-length slice of horizontal distance (default 50 m) after resampling. Unit for the elevation profile, gradient, and comparison. |
| **Climb section** | A run of consecutive profile bins that forms a sustained climb or descent (§5.5). Answers "where are the steep or sustained parts?". |
| **Planned route** | A track describing an intended path. Usually no timestamps; elevation often from a planner's terrain model. |
| **Activity** | A track recorded while actually moving. Has timestamps; elevation from GPS or a barometer. |
| **Analysis** | A versioned, deterministic set of metrics for one track, computed with one parameter set. |
| **Scenario** | A duration estimate from a named method and explicit assumptions. Never an observation. |

## 3. Reference data in this repository

These files motivate the decisions below. Personal activity files belong
in `data/local/` (git-ignored); see ARCHITECTURE.md §10.

| File | Kind | Creator | Points | Spacing (median) | Time | Elevation | Raw step sum | Notes |
|---|---|---|---|---|---|---|---|---|
| `input.gpx` | planned | mapy.com | 531 | 9.5 m | none | integer m, terrain model | 6.80 km | Nebelhorn ascent; 45 steps above 45 % step gradient (max 414 %) caused by 1 m rounding over sub-metre steps |
| `input2.gpx` | planned | mapy.com | 374 | 14.9 m | none | integer m, terrain model | 6.73 km | |
| `Nebelhorn_push.gpx` | activity | StravaGPX | 13,473 | 0.6 m | 1 Hz | 0.1 m, likely barometric | 9.91 km | Same summit as `input.gpx`, different path; 4,539 zero-length steps (stopped) |
| `Gleitweg_Seealpsee_Zeigersattel.gpx` | activity | StravaGPX | 24,827 | 1.0 m | 1 Hz | 0.1 m, likely barometric | 23.64 km | Recording gaps up to 819 s; isolated steps up to 11.6 m/s (GPS jumps) |

Independent reference values reported by Strava for the two activities
(provided by the owner), used to calibrate ADR-007 and ADR-010:

| Activity | Distance | Moving time | Elapsed time | Elevation gain |
|---|---|---|---|---|
| Nebelhorn_push | 8.30 km | 2:28:22 | 3:55:01 | 1,443 m |
| Gleitweg, Seealpsee, Zeigersattel | 20.8 km | 5:01:29 | 7:26:22 | 1,309 m |

ALPHA 0.2.0 gives 8.33 km / 2:29 and 20.86 km / 5:04. Strava's gain is
not reproducible from the exported elevation (ADR-004).

Consequences:

-   Point spacing differs by >10× between sources, so every smoothing
    window must be defined in **metres or seconds, never in point
    counts**.
-   Planned and recorded elevation have different noise, so the
    elevation source must be stored and may select different defaults.
-   A large share of activity steps are stationary, and 1 Hz position
    noise is as large as the distance walked per second. Summing raw
    steps overstates distance by 14--20 % (ADR-010), and moving time
    needs an explicit definition (ADR-007).

## 4. Domain model (Python)

The domain stores points **column-wise** in NumPy arrays rather than as
one object per point. This keeps calculations vectorised, deterministic,
and type-checkable. pandas is allowed in exploration scripts only, not
in `alpha.domain`.

``` python
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import numpy as np
import numpy.typing as npt

class TrackKind(StrEnum):
    PLANNED = "planned"
    ACTIVITY = "activity"
    UNKNOWN = "unknown"

class ElevationSource(StrEnum):
    GPS = "gps"
    BAROMETRIC = "barometric"
    PLANNER_DEM = "planner_dem"     # elevation added by a route planner
    DEM_ENRICHED = "dem_enriched"   # elevation sampled by ALPHA (post-MVP)
    UNKNOWN = "unknown"

class Inference(StrEnum):
    USER = "user"            # user stated it
    HEURISTIC = "heuristic"  # guessed by ALPHA; shown as such
    DEFAULT = "default"      # nothing known

@dataclass(frozen=True)
class Track:
    name: str | None
    kind: TrackKind
    kind_inferred_by: Inference
    elevation_source: ElevationSource
    elevation_source_inferred_by: Inference
    activity_type: str | None             # GPX <type>, e.g. "hiking"
    # Column arrays, all of length n (n >= 0):
    lat_deg: npt.NDArray[np.float64]
    lon_deg: npt.NDArray[np.float64]
    ele_raw_m: npt.NDArray[np.float64]    # NaN where missing
    time_utc_s: npt.NDArray[np.float64]   # POSIX seconds; NaN where missing
    source_segment: npt.NDArray[np.int32] # index of <trkseg>; 0 for <rte>

@dataclass(frozen=True)
class QualityWarning:
    code: str                    # see §8
    message: str
    point_indices: tuple[int, ...] = ()
    count: int | None = None
```

Kind and elevation-source heuristics (always overridable by the user and
recorded as `HEURISTIC`):

-   all points timestamped with plausible spacing → `ACTIVITY`;
-   no timestamps → `PLANNED`;
-   integer-only elevations and no timestamps → `PLANNER_DEM`;
-   `creator` mentions a known planner (e.g. mapy.com) → `PLANNER_DEM`;
-   otherwise `UNKNOWN`.

## 5. Metric definitions

All parameters below are **provisional defaults**. Each is part of the
analysis parameter set (§6) and is settled in the referenced ADR using
the reference data in §3.

### 5.1 Distance (ADR-010)

-   `distance_m`: **jitter-filtered 2D geodesic** distance on the WGS84
    ellipsoid, within source segments only. This is the canonical
    distance for planned and recorded tracks alike.
    -   Within each source segment, the first point is accepted. Each
        later point is accepted once it is at least `distance_min_step_m`
        (default 3 m) from the last accepted point.
    -   Distance is summed between accepted points.
    -   Matches Strava within 0.3 % on both reference activities; changes
        planned routes by ≤ 0.1 %.
-   `distance_raw_m`: unfiltered sum of every within-segment step, kept
    for transparency (14--20 % longer on 1 Hz recordings).
-   `distance_3d_m`: same accepted points, with elevation change included
    (`sqrt(d² + Δh²)`). `None` unless every point has elevation.
-   Steps flagged as GPS jumps (§8) are kept but counted in a warning;
    the warning reports how much distance they contribute.
-   `distance_while_stopped_m` (§5.4) shows how much filtered distance
    falls in stopped time.

### 5.2 Elevation and ascent/descent (ADR-004)

-   `ele_raw_m` is never modified.
-   Ascent/descent use a **hysteresis threshold** per source segment: a
    turning point is confirmed once elevation reverses by at least
    `ascent_threshold_m` (default 3 m) from the running extreme. A
    confirmed climb is counted in full, peak to trough. On the reference
    activities 3 m lowers gain by about 0.5 % (1,384 → 1,377 m); planned
    routes are unaffected.
-   `min_elevation_m`, `max_elevation_m`: from raw elevation. Elevation
    spikes are flagged (§8) but not yet excluded.
-   If any point lacks elevation, all elevation metrics are `None`, not
    partial sums.

### 5.3 Profile bins and gradient (ADR-009)

-   Bins are fixed `profile_bin_m` (default 50 m) slices of the
    **filtered** horizontal cumulative distance (§5.1), which does not
    grow across source-segment boundaries. Bins therefore add up to
    `distance_m`. The last bin may be shorter.
-   Elevation and time are interpolated linearly **per source segment**.
    `ele_change_m` and `elapsed_s` sum only within-segment changes, so a
    jump between segments is never a gradient or time.
    `ele_start_m`/`ele_end_m` are interpolated at the bin edges.
-   `gradient_percent = ele_change_m / bin_length_m * 100`. `None` when
    elevation is incomplete, or for a last bin shorter than half
    `profile_bin_m`.
-   Gradient is **not clipped**. Bins steeper than
    `gradient_outlier_percent` (default 100 %) raise `GRADIENT_OUTLIER`
    with their bin indices; values are not altered.
-   This is a track-derived gradient, not the slope of the surrounding
    terrain.

### 5.4 Time (activities only, ADR-007)

Every second is assigned to exactly one category:
`elapsed_time_s = moving_time_s + stopped_time_s + recording_gap_s`.

-   `elapsed_time_s`: last timestamp − first timestamp.
-   `recording_gap_s`: steps longer than `quality.gap_threshold_s`
    (default 60 s), plus every step crossing a source-segment boundary.
-   **Windowed speed** at each point: 3D displacement across a centred
    window of `moving_window_s` seconds (default 20 s), divided by the
    time it spans. The window never crosses a recording gap.
-   `moving_time_s`: non-gap steps whose starting point's windowed speed
    exceeds `moving_min_speed_m_s` (default 0.3 m/s).
-   `stopped_time_s`: all other non-gap steps.
-   `stop_count`: runs of stopped steps lasting at least
    `stop_min_duration_s` (default 60 s).
-   `distance_while_stopped_m`: filtered distance credited during
    stopped steps (included in `distance_m`).
-   `None` (unknown), never zero, for planned routes, partial timestamps,
    or timestamps running backwards.
-   Matches Strava within 1 % on both reference activities. Movement
    slower than 0.3 m/s (3D) counts as stopped; see ADR-007.

### 5.5 Climb sections

A climb section is a run of profile bins with
`gradient_percent >= climb_min_gradient_percent` (default 8 %) in one
direction.

-   Milder bins are bridged when they total at most
    `climb_max_interruption_m` (default 150 m) in a row.
-   A bin steep in the opposite direction, or without a gradient, always
    ends the section, so up and down sections never overlap.
-   The section starts and ends on a steep bin.
-   The net elevation change in the section's direction (interruptions
    included) must be at least `climb_min_elevation_change_m` (default
    50 m).
-   Each section reports direction, start/end distance, net change,
    mean gradient (signed), steepest bin gradient, and first/last bin.

### 5.6 Gradient distribution

Horizontal distance per gradient band, from profile bins. The bands are
fixed and half-open (`[low, high)`), and the result always contains every
band plus `unknown`, so tracks are comparable:

`< -25 | -25..-15 | -15..-5 | -5..5 | 5..15 | 15..25 | 25..40 | >= 40 | unknown` (%)

### 5.7 Reference elevation from a DEM (ADR-012, ADR-013)

-   Datasets: `COP30` (Copernicus GLO-30, 30 m surface model, downloaded
    via OpenTopography) and `BY_DGM1` (Bavaria, 1 m terrain model, read
    from GeoTIFF tiles the user puts in `<data dir>/dem/BY_DGM1/`; never
    downloaded). Local tiles are read in their own CRS; points outside
    every tile are `NaN`.

-   A DEM is sampled at **knots** about `20 m` apart along the track
    (per source segment: first point, displacement-filter points at
    20 m, last point), bilinearly between pixel centres. Values between
    knots are interpolated linearly along the path, giving one value per
    point (`NaN` where the DEM has no data).
-   Sampling at every 1 Hz point is wrong: lateral GPS jitter on a slope
    becomes vertical noise (Nebelhorn_push DEM gain 1862 m at 1 Hz vs
    1441 m at 20 m).
-   Stored per track and dataset with provenance: dataset, resolution,
    surface/terrain model, vertical datum, attribution, spacing, tiles,
    retrieval time. `ele_raw_m` is never changed.
-   Comparison (`compare_elevation`), recorded minus reference:
    -   `median_difference_m` (offset), `mean_difference_m`,
        `std_difference_m` (shape disagreement);
    -   `p95_abs_difference_m`, `max_abs_difference_m` and its distance;
    -   ascent/descent of both series with the same hysteresis threshold;
    -   **drift** (ADR-014): the difference, median per `bin_m` [100 m],
        then a rolling median over `window_m` [500 m]. Reported as start,
        end, min, and max with positions, and as the range. A range of at
        least `warn_range_m` [20 m] raises `ELEVATION_DRIFT`. These
        `DriftParams` do not affect analyses, so they are not part of
        `AnalysisParams` or the params hash.
-   **Using it in the analysis:** `AnalysisParams.elevation` is
    `"recorded"` (default) or `"dem:<dataset>"`, e.g. `"dem:COP30"`. It
    enters the params hash.
    -   With a DEM, every elevation-dependent value uses that series:
        ascent/descent, min/max, 3D distance, profile, gradient, climbs,
        and the 3D speed behind moving time. The moving-time change is
        < 0.5 min on the reference activities.
    -   The application builds the analysed track with `with_elevation`
        (elevation source `dem_enriched`). `analyse()` refuses a mismatch
        between the setting and the track's elevation source.
    -   A missing profile is an error (`DemProfileMissingError`, API 409
        `DEM_PROFILE_MISSING`), never a silent fallback.
    -   `dem_enriched` cannot be set with `edit`; it is reserved for
        sampled series.

### 5.8 Terrain slope (ADR-015)

-   `terrain_slope_deg` = `atan(|∇z|)` from the DEM at four probes
    `baseline_m` [15 m] north, south, east, and west of each knot (20 m
    spacing, as in §5.7). Values between knots are interpolated along the
    track; NaN where any probe lacks data.
-   It measures the steepness of the **ground around** the track,
    independent of the direction of travel. It is **not** the track
    gradient (§5.4) and not a hazard rating.
-   Summary (`TerrainSlopeSummary`), distance-weighted on the filtered
    distance (each step counts with the slope at its end):
    -   distance per band `<10`, `10-20`, `20-30`, `30-40`, `40-50`,
        `>=50` degrees;
    -   `unknown_m`;
    -   `median_deg`, `p90_deg`, `max_deg`, `max_at_m`.

## 6. Analysis contract

Implemented in `alpha.domain.analysis`; `analyse(track, params) ->
Analysis` is a pure function.

``` python
ALGORITHM_VERSION = "0.3.0"   # 0.3.0 added `elevation`

@dataclass(frozen=True)
class AnalysisParams:                 # validated in __post_init__
    quality: QualityParams = field(default_factory=QualityParams)  # §8.2
    profile_bin_m: float = 50.0
    ascent_threshold_m: float = 3.0
    gradient_outlier_percent: float = 100.0
    distance_min_step_m: float = 3.0  # ADR-010
    moving_window_s: float = 20.0     # ADR-007
    moving_min_speed_m_s: float = 0.3 # ADR-007
    stop_min_duration_s: float = 60.0 # only for stop_count
    climb_min_gradient_percent: float = 8.0
    climb_max_interruption_m: float = 150.0
    climb_min_elevation_change_m: float = 50.0
    elevation: str = "recorded"       # or "dem:<dataset>" (§5.7)

def params_hash(params) -> str:       # SHA-256 of canonical JSON (sorted keys)

@dataclass(frozen=True)
class TrackSummary:                   # None = unknown, never zero
    distance_m: float                 # jitter-filtered
    distance_raw_m: float             # unfiltered
    distance_3d_m: float | None
    ascent_m: float | None
    descent_m: float | None
    min_elevation_m: float | None
    max_elevation_m: float | None
    elapsed_time_s: float | None
    moving_time_s: float | None
    stopped_time_s: float | None
    recording_gap_s: float | None
    stop_count: int | None
    distance_while_stopped_m: float | None

@dataclass(frozen=True)
class ProfileBin:
    index: int
    start_distance_m: float
    end_distance_m: float
    ele_start_m: float | None
    ele_end_m: float | None
    ele_change_m: float | None        # within-segment change only
    gradient_percent: float | None
    elapsed_s: float | None           # activities only

@dataclass(frozen=True)
class ClimbSection:
    direction: Direction              # "up" | "down"
    start_distance_m: float
    end_distance_m: float
    elevation_change_m: float         # net, in the section's direction
    mean_gradient_percent: float      # signed
    max_bin_gradient_percent: float   # signed, steepest in the direction
    start_bin: int
    end_bin: int                      # inclusive

@dataclass(frozen=True)
class Analysis:
    algorithm_version: str
    params: AnalysisParams
    params_hash: str
    summary: TrackSummary
    profile: tuple[ProfileBin, ...]
    climbs: tuple[ClimbSection, ...]
    gradient_distribution: dict[str, float]   # band label -> metres
    warnings: tuple[QualityWarning, ...]      # check_track + GRADIENT_OUTLIER
```

Two analyses are comparable only if `algorithm_version` and
`params_hash` match. The comparison use case recomputes when they do
not.

### 6.1 Report format (`alpha.report`, `alpha analyse --json`)

JSON with `format_version: "11"`:

-   2 added `distance_raw_m` and renamed `distance_in_stops_m` to
    `distance_while_stopped_m`.
-   3 added `elevation_series` to track, comparison (`a`/`b`), and
    scenario reports.
    -   `source` is `recorded` or `dem`. For a DEM it also carries dataset,
        resolution, surface/terrain, vertical datum, sample spacing,
        retrieval time, and **attribution**; for recorded elevation those
        keys are null.
    -   The attribution must be shown wherever DEM-derived values are
        shown.
-   4 added the **elevation check report** (`alpha enrich ID --json`,
    `GET /tracks/{id}/dem/{dataset}`, `POST /tracks/{id}/dem`): track
    reference, `elevation_series`, `tiles`, `downloaded_tiles`,
    `reused_stored_profile`, and `comparison` (calculated, recorded minus
    DEM: median, mean, std, p95/max absolute difference and its position,
    ascent/descent of both series as `*_recorded_m` / `*_dem_m`). Track
    list items gained `dem_datasets` (datasets with a stored profile).
-   5 added `comparison.drift` (range, start, end, min/max and their
    positions, window) and `warnings` (`ELEVATION_DRIFT`) to the
    elevation check report (ADR-014).
-   6 added `terrain_slope` (calculated; bands, unknown, median, p90, max
    and its position, baseline) and `terrain_slope_note` (why it is
    missing) to the elevation check report (ADR-015).
-   7 added the personal baseline report (§7.3, ADR-016).
-   8 restructured it: `models.multiplier` and `models.split` (§7.4,
    ADR-017).
-   9 added `effort` to track records and to the baseline activities,
    and an `effort` section to the baseline report (§7.5, ADR-018).
-   10 added `personal` to scenario reports (§7.6, ADR-019): null for
    preset or user assumptions.
-   11 added the explanation report (§7.7, ADR-020).

The report itself contains:

-   `file`: name, SHA-256, size, creator, GPX version, waypoint count.
-   `warnings`: file-level warnings.
-   `tracks[]`, each with:
    -   `source`: `trk`/`rte` and index;
    -   `read_warnings`;
    -   `track`: kind and elevation source, each with `inferred_by`;
        point count; coverage;
    -   `analysis`: version, params hash, full params;
    -   `summary`;
    -   `gradient_distribution_m`;
    -   `climb_sections`;
    -   `profile`;
    -   `warnings`.
-   Each summary metric is `{"value", "unit", "provenance"}`, where
    provenance is `calculated`, or `unknown` with `value: null`.
-   Floats are rounded only here (0.1 m, 0.1 s, 0.01 %). The output is
    byte-identical for the same input and params.
-   No coordinates are included.

## 7. Comparison and scenario contracts

### 7.1 Comparison

Implemented in `alpha.domain.comparison.compare(track_a, analysis_a,
track_b, analysis_b)`; B is compared against A. Both analyses must share
`algorithm_version` and `params_hash` (otherwise `ValueError`); the
application layer analyses both tracks with the same params, so this
always holds there.

``` python
@dataclass(frozen=True)
class MetricDelta:
    metric: str                    # TrackSummary field, e.g. "ascent_m"
    a: float | None
    b: float | None
    delta: float | None            # b - a; None if either is unknown
    ratio: float | None            # b / a; None if either is unknown or a == 0
    material: bool | None          # None when it cannot be assessed

@dataclass(frozen=True)
class BandDelta:
    band: str                      # gradient band label (§5.6)
    a_m: float
    b_m: float
    delta_m: float
    material: bool

@dataclass(frozen=True)
class ComparisonResult:
    algorithm_version: str
    params_hash: str
    metrics: tuple[MetricDelta, ...]   # distance, 3D distance, ascent, descent,
                                       # min/max elevation, elapsed, moving time
    bands: tuple[BandDelta, ...]       # every band incl. "unknown"
    biggest_climb_a: ClimbSection | None   # largest net ascent (up sections)
    biggest_climb_b: ClimbSection | None
    steepest_climb_a: ClimbSection | None  # highest mean gradient (up sections)
    steepest_climb_b: ClimbSection | None
    notes: tuple[str, ...]
```

-   **"Material"** is a display hint, not a verdict:
    -   `|ratio − 1| ≥ 0.2` for distances, ascent/descent, and times;
    -   `|delta| ≥ 200 m` for lowest and highest point;
    -   `|delta| ≥ 500 m` of distance for a gradient band.
-   **Notes** always explain unknown values, for example "A: time
    metrics are unknown (planned route)". They also flag a different
    elevation source or kind, and always state that differences say
    nothing about conditions, technical difficulty, or safety.
-   Comparisons are recomputed on demand and not persisted. Their JSON
    form is `alpha.report.comparison_report` (`alpha compare --json`).

### 7.2 Scenario (ADR-005)

Implemented in `alpha.domain.scenarios` (formulas, registry) and
`alpha.application.scenarios` (observed times). A scenario is always
**estimated**; the analysis values it uses are **calculated**; recorded
activity times shown beside it are **observed**.

**Structure:** analysis + assumptions (with source) → registered method
→ result. The method is the formula; the assumptions say where its
numbers come from (`user`, `preset`, or, from Phase 7 and outside the
domain, `fitted`).

**Method `din33466`** (registry name, version "1"):

-   `horizontal_s = distance_m / horizontal_speed_m_h * 3600`
-   `vertical_s = (ascent_m / ascent_rate_m_h + descent_m / descent_rate_m_h) * 3600`
-   `moving_s = max(h, v) + min(h, v) / 2`
-   `total_s = moving_s + break_s + delay_s`

`distance_m` is the jitter-filtered 2D distance (§5.1). If ascent or
descent is unknown, `ScenarioInputError` is raised; a duration is never
computed from a missing value.

**Preset** "DIN 33466 standard" = 4000 m/h, 300 m/h up, 500 m/h down,
source `preset`. These are published reference values for an average
hiker on marked trails, not personal values. Overriding any speed
makes the assumptions `user`.

**Range:** each speed multiplier (default 1.2, 1.0, 0.8) scales all three
speeds. Labels are `faster` / `baseline` / `slower` for a symmetric
three-value set, otherwise `x<multiplier>`.

``` python
class AssumptionSource(StrEnum): USER, PRESET, FITTED

@dataclass(frozen=True)
class DinAssumptions:                 # validated: speeds > 0, times >= 0
    horizontal_speed_m_h: float
    ascent_rate_m_h: float
    descent_rate_m_h: float
    break_s: float = 0.0
    delay_s: float = 0.0
    source: AssumptionSource = AssumptionSource.USER
    label: str | None = None          # e.g. "DIN 33466 standard"

@dataclass(frozen=True)
class ScenarioBreakdown:
    horizontal_s: float
    vertical_s: float
    moving_s: float
    break_s: float
    delay_s: float                    # total_s = moving + break + delay

@dataclass(frozen=True)
class DurationScenario:
    label: str
    speed_multiplier: float
    estimated_duration_s: float
    breakdown: ScenarioBreakdown

@dataclass(frozen=True)
class ScenarioInputs:                 # calculated values used by the method
    distance_m: float
    ascent_m: float
    descent_m: float

@dataclass(frozen=True)
class ScenarioResult:
    method: str                       # registry name, e.g. "din33466"
    method_version: str
    assumptions: DinAssumptions
    algorithm_version: str            # of the analysis used
    params_hash: str
    inputs: ScenarioInputs
    scenarios: tuple[DurationScenario, ...]
    uncertainty_notes: tuple[str, ...]
```

**Uncertainty notes**:

-   Always: what the estimate does not include.
-   For a preset: that the values are not personal.
-   For a range: that it reflects pace only.
-   With 0 breaks: that no breaks are included.
-   When the route has distance steeper than 25 %: that steepness beyond
    total ascent and descent is not modelled.

**Observed times** (application layer):

-   An activity shows its own recorded moving and elapsed time.
-   A planned route shows those of every activity linked to it.
-   Each carries an **implied speed multiplier**: DIN baseline moving
    time on that *activity's own* distance and ascent, divided by its
    recorded moving time. This is a single reference point, never
    presented as a personal model.

Scenarios are recomputed on demand and not stored (see §10). JSON:
`alpha.report.scenario_report` (`alpha scenarios --json`).

Invariant tests: lowering any speed never shortens a scenario; breaks
and delay add exactly their duration; moving time scales with
1 / multiplier.

### 7.3 Personal speed baseline (ADR-016)

``` python
@dataclass(frozen=True)
class ActivitySample:          # one eligible activity, one elevation series
    track_id: int
    group: int                 # route group: linked plan's ID, else own ID
    name: str | None
    started_at_utc: datetime | None
    distance_m: float          # jitter-filtered 2D distance (§5.2)
    ascent_m: float
    descent_m: float
    moving_time_s: float       # > 0

@dataclass(frozen=True)
class BaselineParams:
    min_groups: int = 5                     # distinct routes before fitting
    min_moving_s: float = 1800.0
    activity_types: tuple[str, ...] = ("hiking", "walking")
    reference: DinAssumptions = DIN_33466_STANDARD
```

-   Per activity (observed ratio):
    `k_i = DIN moving time(reference) / moving_time_s`.
-   Fit (`MultiplierFit`):
    -   `multiplier = exp(mean over groups of mean ln k_i)`;
    -   `spread` = sample standard deviation of the group means of
        `ln k_i`;
    -   `average_interval` = `exp(mean ± t975(n−1)·spread/√n)`;
    -   `single_interval` = `exp(mean ± t975(n−1)·spread·√(1+1/n))`;
        `n` = number of groups.
-   `validation`: leave one route group out. `error_ratio` =
    `predicted / observed − 1` per activity; reported as the median and
    maximum of the absolute value.
-   `assumptions`: the reference speeds × `multiplier`, source `fitted`.
-   Status `not enough data` when `n < min_groups`. Then only the
    per-activity ratios are reported.
-   Exclusions carry a reason: activity type, no timestamps, moving time
    < `min_moving_s`, ascent unknown, or the requested DEM series not
    stored.
-   JSON: `alpha.report.baseline_report` (`alpha baseline --json`,
    report format 8, `models.multiplier`).

### 7.4 Flat and vertical factors (ADR-017)

-   Per activity, at the reference speeds:
    -   `h_i = distance_m / H · 3600`;
    -   `v_i = (ascent_m / U + descent_m / D) · 3600`.
-   Model: `moving = max(h/kh, v/kv) + min(h/kh, v/kv) / 2`. The ascent
    and descent rates share `kv`, keeping DIN's U : D ratio.
-   Fit (`SplitFit`):
    -   minimises `Σ w_i · ln(pred_i / obs_i)²`, with `w_i = 1 / (size of
        its route group)`;
    -   grid over `ln k` from ln 0.2 to ln 8, step 0.02, then 0.001;
    -   an optimum on the edge gives `not identifiable`.
-   Gates (`BaselineParams`):
    -   `split_min_groups` [8] distinct routes;
    -   `ratio_range` = max/min over groups of the geometric-mean
        `v_i / h_i` must be ≥ `split_min_ratio_range` [1.5], otherwise
        `not identifiable`.
-   Reported:
    -   `horizontal_factor`, `vertical_factor`;
    -   95 % jackknife intervals over route groups;
    -   `residual_spread` (std of group mean log errors, `ddof=2`);
    -   leave-one-route-out `validation`;
    -   `assumptions`: `H·kh`, `U·kv`, `D·kv`, source `fitted`.
-   JSON: `models.split`, with `reason` when nothing is fitted. Each
    activity carries `vertical_horizontal_ratio`, `loo_error_multiplier`,
    and `loo_error_split`.

### 7.5 Effort (ADR-018)

-   `Effort` = `easy` | `normal` | `hard`, a user annotation per activity
    (`TrackRecord.effort`, `ActivitySample.effort`); null = not stated.
-   `BaselineParams.effort` selects what both models are fitted on:
    -   `normal` (default): normal and not stated;
    -   `hard`;
    -   `easy`;
    -   `all`.
-   Activities outside the selection are excluded with the reason
    `effort … (fitting '…' efforts)`, and reported:
    -   `effort_groups`: per label, `n_activities` and the **observed**
        geometric mean of `k_i`;
    -   `pace_vs_models`: per activity, DIN moving time with the fitted
        assumptions / observed moving time, for model 1 and model 2
        (null when a model is not fitted; > 1 = faster than predicted).
-   JSON `effort`: `fitted_on`, `included`, `observed_by_effort`,
    `outside_fit`.

### 7.6 Personal assumptions in scenarios (ADR-019)

-   `personal_assumptions(BaselineView) -> PersonalAssumptions`:
    -   picks the fitted model with the smallest leave-one-route-out
        median error (model 1 on a tie);
    -   raises `BaselineNotFittedError` when none is fitted.
-   `PersonalAssumptions`:
    -   `assumptions` (source `fitted`);
    -   `model` (`multiplier` | `split`);
    -   `n_activities`, `n_routes`, `effort`, `median_abs_error`;
    -   `range_multipliers` = (faster, 1, slower), from the single-hike
        95 % range:
        -   model 1: `single_interval / k`;
        -   model 2: `exp(±t975(n−2)·residual_spread·√(1+1/n))`;
    -   `fitted_track_ids`.
-   `estimate_personal(ws, track_id, params, baseline_params, break_s,
    delay_s, speed_multipliers=None)`: fits with the same `params` as the
    estimate, adds the user's breaks and delay, and adds notes for what
    the fit rests on, what the range means, and whether the track is part
    of the fit.
-   JSON `personal`: `provenance` = `fitted`, `model`, `n_activities`,
    `n_routes`, `effort`, `loo_median_abs_error`, `range_multipliers`,
    `range_meaning`.

### 7.7 Explanations (ADR-020)

-   `Fact`: `key` (dotted, stable), `label`, `value`, `unit` (`duration`,
    `km`, `m`, `m/h`, `km/h`, `factor`, `percent`, `deg`, `count`, or
    `text`), `provenance` (`calculated`, `estimated`, `observed`,
    `fitted`, `reference`, or `label`), and `display()` (e.g. `3:53 h`,
    `8.33 km`, `x1.46`).
-   `Statement`: `text`, `facts` (the keys it cites), and `caveat`
    (a fixed qualification, never sent to a model and shown verbatim).
-   `explain(topic, report, rewrite=None, model=None, language="English")
    -> Explanation`:
    -   `topic` is `scenarios`, `elevation`, or `baseline`;
    -   `report` is the JSON of `alpha scenarios`, `alpha enrich`, or
        `alpha baseline` (`--json`).
-   `Explanation`:
    -   `topic`, `statements`;
    -   `source` (`template` | `model`), `model`;
    -   `boundary` (fixed text from ALPHA);
    -   `fallback_reasons` (why a model answer was not used), `facts`.
-   Validation of model text (ADR-020):
    -   cited keys must exist;
    -   durations must match a duration fact within 1 min;
    -   other numbers must match a fact within half a unit of its display
        precision, including derived forms (a factor as a percentage);
    -   forbidden topics are rejected;
    -   at least as many statements as the draft (without caveats);
    -   per statement, every cited numeric fact must be stated, and every
        number must belong to a cited fact or be template wording;
    -   every numeric fact of the draft must reappear.
-   JSON: `alpha.report.explanation_report`: `topic`, `source`, `model`,
    `statements[{text, facts, caveat}]`, `boundary`, `fallback_reasons`,
    `notes`,
    and `facts[{key, label, display, provenance}]`.

## 8. Data quality

### 8.1 Read errors (`alpha.gpx.reader.GpxReadError`)

Raised only when nothing can be read. Everything else is a warning.

| Code | Cause |
|---|---|
| `EMPTY_FILE` | Zero bytes |
| `FILE_TOO_LARGE` | Above `max_bytes` (default 50 MB) |
| `UNSUPPORTED_ENCODING` | Declared encoding unknown to Python |
| `DECODE_ERROR` | Bytes invalid for the detected encoding |
| `INVALID_XML` | Not well-formed XML |
| `INVALID_GPX` | Well-formed XML that gpxpy rejects (e.g. non-numeric `lat`) |

Encoding is taken from the byte-order mark, then the XML declaration,
else UTF-8. gpxpy itself ignores the declaration, so the reader decodes.

### 8.2 Warnings

Thresholds live in `QualityParams` (provisional defaults in brackets).

``` python
@dataclass(frozen=True)
class QualityParams:
    gap_threshold_s: float = 60.0
    jump_speed_m_s: float = 5.0          # hiking
    max_vertical_speed_m_s: float = 3.0  # reference activities peak at 1.4 m/s
    source_segment_gap_m: float = 100.0
```

| Code | Trigger | Raised by |
|---|---|---|
| `NO_TRACKS` | No `<trk>` or `<rte>` with at least one valid point | reader (file) |
| `INVALID_COORDINATE` | Latitude/longitude missing, non-finite, or out of range; point excluded. Indices are positions in the file | reader (track) |
| `INSUFFICIENT_POINTS` | Fewer than 2 points | `check_track` |
| `MISSING_ELEVATION` | Any point without `<ele>`; coverage % reported | `check_track` |
| `MISSING_TIMESTAMPS` | Some but not all points have `<time>` | `check_track` |
| `DUPLICATE_POINTS` | Consecutive points with identical coordinates, elevation and time | `check_track` |
| `NON_MONOTONIC_TIME` | Timestamp not later than the previous one (exact duplicates excluded) | `check_track` |
| `RECORDING_GAP` | Step duration > `gap_threshold_s` [60 s] within a source segment | `check_track` |
| `GPS_JUMP_DETECTED` | Step speed > `jump_speed_m_s` [5 m/s] within a source segment | `check_track` |
| `ELEVATION_SPIKE_DETECTED` | Vertical speed > `max_vertical_speed_m_s` [3 m/s] within a source segment | `check_track` |
| `SOURCE_SEGMENT_GAP` | Boundary between source segments spans > `source_segment_gap_m` [100 m] | `check_track` |
| `KIND_INFERRED` / `ELEVATION_SOURCE_INFERRED` | Value guessed by heuristic, not stated by the user | `check_track` |
| `GRADIENT_OUTLIER` | Profile-bin gradient steeper than ±`gradient_outlier_percent` [100 %]; uses `bin_indices` | `analyse` |
| `ELEVATION_DRIFT` | Smoothed recorded-minus-reference difference (median per 100 m, rolling median over 500 m) spans ≥ `warn_range_m` [20 m] (ADR-014) | `compare_elevation` (elevation check only) |

Conventions:

-   Point indices refer to the track arrays; for step-based warnings
    the index is the first point of the step. Analysis warnings refer
    to profile bins through `bin_indices` instead.
-   Time-based checks need both timestamps of a step, so planned routes
    never get them.
-   Steps that cross a source-segment boundary are excluded from gap,
    jump, and spike checks.
-   Points are never silently dropped. An excluded point is counted in
    a warning with its index.

## 9. Provenance categories

Used consistently in code, API, and UI (matching PROJECT_VISION.md):

-   **Observed:** present in the GPX: coordinates, timestamps, raw
    elevation.
-   **Calculated:** deterministically derived: distance, ascent,
    gradient, moving time.
-   **Estimated:** produced by a model with assumptions: scenario
    durations.
-   **Unknown:** not available or not inferable: e.g. moving time of a
    planned route. Represented as `None`, never zero.

## 10. Persistence (SQLite, MVP; ADR-002)

Implemented in `alpha.persistence.schema` (schema version 4; additive
upgrades from older versions run automatically). The data
directory (default `data/local/`, git-ignored) holds `alpha.db` and
`files/<sha256>.gpx`, an unmodified copy of every imported file. IDs are
integers. Times are stored as naive UTC.

``` text
meta (schema_version)
source_files 1 ── * tracks 1 ── * track_points
                    tracks 1 ── * analyses 1 ── * profile_bins
                                 analyses 1 ── * climb_sections
                    tracks (activity) * ── 0..1 tracks (planned)   via planned_track_id
```

### `source_files`

-   `id`, `sha256` (unique; makes imports idempotent),
    `original_filename`, `stored_path` (relative to the data dir),
    `size_bytes`
-   `creator`, `gpx_version`, `waypoint_count`, `parser_name`,
    `parser_version`, `warnings_json` (file-level), `imported_at_utc`

### `tracks`

-   `id`, `source_file_id` (cascade), `gpx_element` (`trk` | `rte`),
    `index_in_element`
-   `name`, `activity_type`
-   `kind`, `kind_inferred_by`, `elevation_source`,
    `elevation_source_inferred_by` (`user` after `alpha edit`)
-   `planned_track_id` (nullable; set by the user to link an activity to
    the plan it followed; set to NULL when the plan is deleted)
-   `point_count`, `source_segment_count`, `has_elevation`,
    `has_timestamps`, `started_at_utc`, `read_warnings_json`

### `track_points` (WITHOUT ROWID, PK `track_id, idx`)

-   `track_id`, `idx`, `source_segment`, `lat_deg`, `lon_deg`,
    `ele_raw_m` (NULL = missing), `time_utc_s` (POSIX seconds; NULL =
    missing)

Relational rows are acceptable at this scale (≈25k points per long
activity). The original GPX remains the source of truth.

### `analyses` (unique `track_id, algorithm_version, params_hash`)

-   `id`, `track_id` (cascade), `algorithm_version`, `params_hash`,
    `params_json`
-   one column per `TrackSummary` field
-   `gradient_distribution_json`, `warnings_json`, `created_at_utc`

A cache: missing rows are computed on demand. Rows are deleted when the
user edits the track's kind or elevation source.

### `profile_bins` (WITHOUT ROWID, PK `analysis_id, bin_index`)

-   `start_distance_m`, `end_distance_m`, `ele_start_m`, `ele_end_m`,
    `ele_change_m`, `gradient_percent`, `elapsed_s`

### `climb_sections` (WITHOUT ROWID, PK `analysis_id, section_index`)

-   `direction`, `start_distance_m`, `end_distance_m`,
    `elevation_change_m`, `mean_gradient_percent`,
    `max_bin_gradient_percent`, `start_bin`, `end_bin`

### `dem_profiles` (schema 2, unique `track_id, dataset_id`; ADR-012)

-   `track_id` (cascade), `dataset_id`, `dataset_name`, `resolution_m`,
    `surface`, `vertical_datum`, `attribution`, `sample_spacing_m`,
    `tiles_json`, `retrieved_at_utc`, `created_at_utc`, `point_count`
-   `elevation_blob`: one float64 (little-endian) per track point; NaN =
    no data

### `track_annotations` (schema 4; ADR-018)

-   `track_id` (primary key, cascade), `effort` (`easy` | `normal` |
    `hard` | null), `updated_at_utc`
-   User annotations that the GPX does not contain; set only by
    `alpha edit` / `PATCH`, never inferred.

### `terrain_slopes` (schema 3, unique `track_id, dataset_id`; ADR-015)

-   `track_id` (cascade), `dataset_id`, `baseline_m`, `sample_spacing_m`,
    `created_at_utc`, `point_count`
-   `slope_blob`: one float64 (little-endian) per track point, degrees;
    NaN = unknown

DEM tiles themselves are cached as files in `<data dir>/dem/<dataset>/`
with a JSON sidecar (bounds, retrieval time). Local tiles (`BY_DGM1`) are
indexed in `_index.json` (ADR-013).

### `scenarios` (deferred; not created, ADR-005)

-   `id`, `analysis_id`, `method`, `method_version`, `assumptions_json`,
    `results_json`, `created_at_utc`

Deleting a source file cascades to all derived rows and removes the
stored GPX copy.
