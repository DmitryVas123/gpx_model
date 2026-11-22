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

| File | Kind | Creator | Points | Spacing (median) | Time | Elevation | Distance | Notes |
|---|---|---|---|---|---|---|---|---|
| `input.gpx` | planned | mapy.com | 531 | 9.5 m | none | integer m, terrain model | 6.80 km | Nebelhorn ascent; 45 steps above 45 % step gradient (max 414 %) caused by 1 m rounding over sub-metre steps |
| `input2.gpx` | planned | mapy.com | 374 | 14.9 m | none | integer m, terrain model | 6.73 km | |
| `Nebelhorn_push.gpx` | activity | StravaGPX | 13,473 | 0.6 m | 1 Hz | 0.1 m, likely barometric | 9.91 km | Same summit as `input.gpx`, different path; 4,539 zero-length steps (stopped) |
| `Gleitweg_Seealpsee_Zeigersattel.gpx` | activity | StravaGPX | 24,827 | 1.0 m | 1 Hz | 0.1 m, likely barometric | 23.64 km | Recording gaps up to 819 s; isolated steps up to 11.6 m/s (GPS jumps) |

Consequences:

-   Point spacing differs by >10× between sources, so every smoothing
    window must be defined in **metres or seconds, never in point
    counts**.
-   Planned and recorded elevation have different noise, so the
    elevation source must be stored and may select different defaults.
-   A large share of activity steps are stationary; moving time and GPS
    jitter while stopped must be handled explicitly.

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

### 5.1 Distance

-   `distance_m`: sum of **2D geodesic** step lengths on the WGS84
    ellipsoid, within source segments only. This is the canonical
    distance.
-   `distance_3d_m`: same with elevation change included; reported as
    secondary (1--3 % longer on the reference routes).
-   Steps flagged as GPS jumps (§8) are kept but counted in a warning;
    the warning reports how much distance they contribute.
-   Known limitation: GPS jitter while stationary adds distance on
    activities. Whether stationary intervals are excluded is decided in
    ADR-007.

### 5.2 Elevation and ascent/descent

-   `ele_raw_m` is never modified. Processed elevation is a separate
    array.
-   Ascent/descent use a **hysteresis threshold**: a change counts only
    once elevation has moved at least `ascent_threshold_m` from the last
    accepted reference point. Default: 3 m for all sources (ADR-004).
    On the reference activities this changes gain by about 1 %
    (1,384 → 1,372 m).
-   `min_elevation_m`, `max_elevation_m` come from raw elevation, with
    flagged spikes excluded and reported.
-   If any source segment lacks elevation, ascent/descent are `None`
    for the track, not partial sums, unless a coverage percentage is
    reported with them.

### 5.3 Profile bins and gradient

-   The track is resampled every `profile_bin_m` metres of horizontal
    distance (default 50 m) by linear interpolation along the track.
-   `gradient_percent = (ele_end_m - ele_start_m) / horizontal_length_m * 100`
    per profile bin.
-   Gradient is **not clipped**. Bins above `gradient_outlier_percent`
    (default 100 %) are flagged `GRADIENT_OUTLIER` for review, not
    altered.
-   This is a track-derived gradient, not the slope of the surrounding
    terrain.

### 5.4 Time (activities only)

-   `elapsed_time_s`: last timestamp − first timestamp.
-   `recording_gap_s`: sum of step durations above `gap_threshold_s`
    (default 60 s); usually auto-pause or signal loss.
-   `moving_time_s`: sum of step durations that are not gaps and not
    inside a stop. A **stop** is an interval of at least
    `stop_min_duration_s` (default 60 s) during which the track stays
    within `stop_radius_m` (default 10 m) of its start. A speed
    threshold alone is not used, because steep uphill walking can be
    slower than 0.3 m/s horizontally (ADR-007).
-   `stopped_time_s = elapsed_time_s - moving_time_s - recording_gap_s`.
-   For planned routes these values are `None` (unknown), never zero.

### 5.5 Climb sections

A climb section is a maximal run of profile bins with
`gradient_percent >= climb_min_gradient_percent` (default 8 %),
allowing interruptions of up to `climb_max_interruption_m` (default
150 m), and kept only if its net ascent is at least
`climb_min_ascent_m` (default 50 m). Descents are defined
symmetrically. Each section reports start/end distance, length, ascent,
mean gradient, and steepest bin.

### 5.6 Gradient distribution

Share of horizontal distance per gradient band, computed from profile
bins. Fixed bands so tracks are comparable:

`< -25 | -25..-15 | -15..-5 | -5..5 | 5..15 | 15..25 | 25..40 | > 40` (%)

## 6. Analysis contract

``` python
@dataclass(frozen=True)
class AnalysisParams:
    quality: QualityParams = QualityParams()   # §8; implemented in Phase 1
    profile_bin_m: float = 50.0
    ascent_threshold_m: float = 3.0
    gradient_outlier_percent: float = 100.0
    stop_min_duration_s: float = 60.0
    stop_radius_m: float = 10.0
    climb_min_gradient_percent: float = 8.0
    climb_max_interruption_m: float = 150.0
    climb_min_ascent_m: float = 50.0

@dataclass(frozen=True)
class TrackSummary:
    distance_m: float
    distance_3d_m: float | None
    ascent_m: float | None
    descent_m: float | None
    min_elevation_m: float | None
    max_elevation_m: float | None
    elapsed_time_s: float | None
    moving_time_s: float | None
    stopped_time_s: float | None
    recording_gap_s: float | None

@dataclass(frozen=True)
class ProfileBin:
    index: int
    start_distance_m: float
    end_distance_m: float
    ele_start_m: float | None
    ele_end_m: float | None
    gradient_percent: float | None
    elapsed_s: float | None        # activities only

@dataclass(frozen=True)
class ClimbSection:
    direction: str                 # "up" | "down"
    start_distance_m: float
    end_distance_m: float
    ascent_m: float                # net, positive for both directions
    mean_gradient_percent: float
    max_bin_gradient_percent: float

@dataclass(frozen=True)
class Analysis:
    algorithm_version: str         # e.g. "0.1.0"
    params: AnalysisParams
    params_hash: str               # SHA-256 of canonical JSON of params
    summary: TrackSummary
    profile: tuple[ProfileBin, ...]
    climbs: tuple[ClimbSection, ...]
    gradient_distribution: dict[str, float]   # band label -> distance_m
    warnings: tuple[QualityWarning, ...]
```

Two analyses are comparable only if `algorithm_version` and
`params_hash` match. The comparison use case recomputes when they do
not.

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
| `GRADIENT_OUTLIER` | Profile-bin gradient > `gradient_outlier_percent` | analysis (Phase 2) |

Conventions:

-   Point indices refer to the track arrays; for step-based warnings
    the index is the first point of the step.
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
    `ele_start_m`, `ele_end_m`, `gradient_percent`, `elapsed_s`

### `climb_sections`

-   `analysis_id`, `section_index`, `direction`, `start_distance_m`,
    `end_distance_m`, `ascent_m`, `mean_gradient_percent`,
    `max_bin_gradient_percent`

### `scenarios`

-   `id`, `analysis_id`, `method`, `method_version`, `assumptions_json`,
    `results_json`, `created_at_utc`

Deleting a source file cascades to all derived rows and removes the
stored GPX.
