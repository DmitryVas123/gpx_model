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

## 6. Analysis contract

Implemented in `alpha.domain.analysis`; `analyse(track, params) ->
Analysis` is a pure function.

``` python
ALGORITHM_VERSION = "0.2.0"

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

JSON with `format_version: "2"` (2 added `distance_raw_m` and renamed
`distance_in_stops_m` to `distance_while_stopped_m`):

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

``` python
@dataclass(frozen=True)
class MetricDelta:
    metric: str                    # e.g. "ascent_m"
    a: float | None
    b: float | None
    delta: float | None            # b - a; None if either is unknown
    ratio: float | None            # b / a; None if a is 0 or unknown

@dataclass(frozen=True)
class ComparisonResult:
    track_a_id: str
    track_b_id: str
    algorithm_version: str
    params_hash: str
    deltas: tuple[MetricDelta, ...]
    gradient_distribution_a: dict[str, float]
    gradient_distribution_b: dict[str, float]
    steepest_climb_a: ClimbSection | None
    steepest_climb_b: ClimbSection | None
    notes: tuple[str, ...]          # e.g. "B has no timestamps; time metrics unknown"
```

"Material difference" (Journey C) is reported per metric when
`|ratio - 1| >= 0.2` or a gradient band differs by ≥ 500 m. These
thresholds are display hints, not verdicts.

Comparisons are recomputed on demand and not persisted in the MVP.

### 7.2 Scenario

Scenarios use a **named method** (Strategy pattern). The MVP ships one
published, transparent method; others can be added later.

**Method `din33466`** (German hiking-time standard, also used by Alpine
clubs):

-   `t_horizontal = distance_m / horizontal_speed_m_h`
-   `t_vertical = ascent_m / ascent_rate_m_h + descent_m / descent_rate_m_h`
-   `t_moving = max(t_h, t_v) + min(t_h, t_v) / 2`
-   `t_total = t_moving + break_s + delay_s`

The published standard values (4 km/h, 300 m/h up, 500 m/h down) are a
**labelled preset**, not a default truth. Users set their own values.

A range is produced by applying explicit multipliers to the speeds
(user-set; e.g. 0.8 / 1.0 / 1.2). With these defaults the method gives
≈5.4 h for `input.gpx`, while `Nebelhorn_push.gpx` (a different path to
the same summit) recorded about 2.5 h moving time (gpxpy's
moving-time definition, not yet §5.4). This shows how strongly the
answer depends on assumptions, which is why assumptions are always
displayed.

``` python
@dataclass(frozen=True)
class DinAssumptions:
    horizontal_speed_m_h: float    # > 0
    ascent_rate_m_h: float         # > 0
    descent_rate_m_h: float        # > 0
    break_s: float = 0.0           # >= 0
    delay_s: float = 0.0           # >= 0
    preset_name: str | None = None # e.g. "DIN 33466 standard"

@dataclass(frozen=True)
class ScenarioBreakdown:
    horizontal_s: float
    vertical_s: float
    moving_s: float
    break_s: float
    delay_s: float

@dataclass(frozen=True)
class DurationScenario:
    label: str                     # "faster" | "baseline" | "slower"
    speed_multiplier: float
    estimated_duration_s: float
    breakdown: ScenarioBreakdown

@dataclass(frozen=True)
class ScenarioResult:
    method: str                    # "din33466"
    method_version: str
    assumptions: DinAssumptions
    based_on_analysis: str         # algorithm_version + params_hash
    scenarios: tuple[DurationScenario, ...]
    uncertainty_notes: tuple[str, ...]
```

Invariant tests: lowering any speed never shortens a scenario; adding
breaks increases it by exactly the break time.

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

## 10. Persistence (SQLite, MVP)

``` text
source_files 1 ── * tracks 1 ── * track_points
                    tracks 1 ── * analyses 1 ── * profile_bins
                                 analyses 1 ── * climb_sections
                                 analyses 1 ── * scenarios
                    tracks (activity) * ── 0..1 tracks (planned)   via planned_track_id
```

### `source_files`

-   `id` (UUID), `sha256` (unique; makes imports idempotent),
    `original_filename`, `stored_path` (relative), `size_bytes`
-   `creator`, `gpx_version`, `parser_name`, `parser_version`
-   `imported_at_utc`

### `tracks`

-   `id`, `source_file_id`, `index_in_file`, `gpx_element` (`trk` | `rte`)
-   `name`, `activity_type`
-   `kind`, `kind_inferred_by`
-   `elevation_source`, `elevation_source_inferred_by`
-   `planned_track_id` (nullable; set by the user to link an activity to
    the plan it followed)
-   `point_count`, `source_segment_count`, `has_elevation`,
    `has_timestamps`, `started_at_utc`
-   `bbox_min_lat`, `bbox_min_lon`, `bbox_max_lat`, `bbox_max_lon`

### `track_points` (WITHOUT ROWID, PK `track_id, idx`)

-   `track_id`, `idx`, `source_segment`, `lat_deg`, `lon_deg`,
    `ele_raw_m`, `time_utc`

Relational rows are acceptable at this scale (≈25k points per long
activity). The original GPX remains the source of truth; points can be
rebuilt from it.

### `analyses` (unique `track_id, algorithm_version, params_hash`)

-   `id`, `track_id`, `algorithm_version`, `params_json`, `params_hash`
-   summary columns from `TrackSummary`
-   `gradient_distribution_json`, `warnings_json`, `created_at_utc`

### `profile_bins`

-   `analysis_id`, `bin_index`, `start_distance_m`, `end_distance_m`,
    `ele_start_m`, `ele_end_m`, `ele_change_m`, `gradient_percent`,
    `elapsed_s`

### `climb_sections`

-   `analysis_id`, `section_index`, `direction`, `start_distance_m`,
    `end_distance_m`, `elevation_change_m`, `mean_gradient_percent`,
    `max_bin_gradient_percent`, `start_bin`, `end_bin`

### `scenarios`

-   `id`, `analysis_id`, `method`, `method_version`, `assumptions_json`,
    `results_json`, `created_at_utc`

Deleting a source file cascades to all derived rows and removes the
stored GPX.
