"""Pydantic schemas of the HTTP API (DATA_MODEL.md §6.1, ADR-011).

Responses are produced by ``alpha.report`` (the single place that turns domain
results into JSON) and validated against these models. ``extra="forbid"``
makes any drift between the two fail in tests instead of silently changing the
documented API.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from alpha.domain.track import Effort, ElevationSource, TrackKind


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- shared ---------------------------------------------------------------------


class ErrorBody(Strict):
    code: str = Field(description="machine-readable error code, e.g. TRACK_NOT_FOUND")
    message: str
    details: list[dict[str, Any]] | None = None


class ErrorResponse(Strict):
    error: ErrorBody


class Health(Strict):
    status: Literal["ok"]
    version: str
    profile: str
    synthetic_data: bool = Field(
        description="the active profile is for generated hikes (ADR-021)"
    )


class ProfileItem(Strict):
    name: str
    synthetic_data: bool
    active: bool


class Profiles(Strict):
    active: str
    profiles: list[ProfileItem]


class ProfileSelection(Strict):
    name: str


class Metric(Strict):
    value: float | int | None = Field(description="null means unknown, never zero")
    unit: str
    provenance: Literal["calculated", "unknown"]


class Summary(Strict):
    distance_m: Metric
    distance_raw_m: Metric
    distance_3d_m: Metric
    ascent_m: Metric
    descent_m: Metric
    min_elevation_m: Metric
    max_elevation_m: Metric
    elapsed_time_s: Metric
    moving_time_s: Metric
    stopped_time_s: Metric
    recording_gap_s: Metric
    stop_count: Metric
    distance_while_stopped_m: Metric


class Warning(Strict):
    code: str
    message: str
    count: int | None = None
    point_indices: list[int] | None = None
    bin_indices: list[int] | None = None


class InferredValue(Strict):
    value: str
    inferred_by: Literal["user", "heuristic", "default"]


class Climb(Strict):
    direction: Literal["up", "down"]
    start_distance_m: float
    end_distance_m: float
    elevation_change_m: float
    mean_gradient_percent: float
    max_bin_gradient_percent: float
    start_bin: int
    end_bin: int


class ProfileBin(Strict):
    index: int
    start_distance_m: float
    end_distance_m: float
    ele_start_m: float | None
    ele_end_m: float | None
    ele_change_m: float | None
    gradient_percent: float | None
    elapsed_s: float | None


class ElevationSeries(Strict):
    """Which elevation the elevation-dependent values use (ADR-012)."""

    source: Literal["recorded", "dem"]
    dataset_id: str | None = None
    dataset_name: str | None = None
    resolution_m: float | None = None
    surface: str | None = None
    vertical_datum: str | None = None
    sample_spacing_m: float | None = None
    retrieved_at_utc: str | None = None
    attribution: str | None = Field(
        default=None, description="must be shown with any DEM-derived value"
    )


class ElevationSeriesPair(Strict):
    a: ElevationSeries
    b: ElevationSeries


class StoredRef(Strict):
    id: int
    source_filename: str
    planned_track_id: int | None


# --- tracks ---------------------------------------------------------------------


class TrackRecord(Strict):
    id: int
    source_file_id: int
    source_filename: str
    gpx_element: Literal["trk", "rte"]
    index_in_element: int
    name: str | None
    activity_type: str | None
    kind: InferredValue
    elevation_source: InferredValue
    planned_track_id: int | None
    point_count: int
    has_elevation: bool
    has_timestamps: bool
    started_at_utc: str | None = Field(description="ISO 8601, UTC")
    effort: Effort | None = Field(description="set by the user only (ADR-018)")
    imported_at_utc: str | None
    read_warnings: list[Warning]


class TrackListItem(Strict):
    record: TrackRecord
    summary: Summary
    dem_datasets: list[str] = Field(description="datasets with a stored DEM profile")


class TrackInfo(Strict):
    name: str | None
    activity_type: str | None
    kind: InferredValue
    elevation_source: InferredValue
    point_count: int
    source_segment_count: int
    elevation_coverage: float | None
    time_coverage: float | None


class AnalysisInfo(Strict):
    algorithm_version: str
    params_hash: str
    params: dict[str, Any]


class TrackAnalysis(Strict):
    """Full analysis of a stored track (``alpha show --json``)."""

    stored: StoredRef
    elevation_series: ElevationSeries
    track: TrackInfo
    analysis: AnalysisInfo
    summary: Summary
    gradient_distribution_m: dict[str, float | None]
    climb_sections: list[Climb]
    profile: list[ProfileBin]
    warnings: list[Warning]


class SourceFileInfo(Strict):
    id: int
    sha256: str
    original_filename: str
    size_bytes: int


class ImportResult(Strict):
    created: bool = Field(description="false: identical bytes were already imported")
    source_file: SourceFileInfo
    tracks: list[TrackRecord]


class TrackEdit(Strict):
    """User corrections; values set here are stored with inferred_by=user."""

    kind: TrackKind | None = None
    elevation_source: ElevationSource | None = None
    planned_track_id: int | None = Field(
        default=None, description="link this activity to a planned route"
    )
    clear_planned_track: bool = False
    effort: Effort | None = Field(
        default=None, description="your own judgement of the activity (ADR-018)"
    )
    clear_effort: bool = False


class Deletion(Strict):
    source_file_id: int
    source_filename: str
    tracks: list[TrackRecord]
    deleted: bool


# --- comparison ---------------------------------------------------------------------


class MetricComparison(Strict):
    metric: str
    unit: str
    a: float | int | None
    b: float | int | None
    delta: float | int | None
    ratio: float | None
    material: bool | None = Field(description="display hint, not a verdict")


class BandComparison(Strict):
    band: str
    a: float | None
    b: float | None
    delta: float | None
    material: bool


class ClimbPair(Strict):
    a: Climb | None
    b: Climb | None


class AnalysisRef(Strict):
    algorithm_version: str
    params_hash: str


class Comparison(Strict):
    format_version: str
    a: StoredRef
    b: StoredRef
    analysis: AnalysisRef
    metrics: list[MetricComparison]
    gradient_bands_m: list[BandComparison]
    biggest_climb: ClimbPair
    steepest_climb: ClimbPair
    notes: list[str]
    elevation_series: ElevationSeriesPair


# --- scenarios ---------------------------------------------------------------------


class Method(Strict):
    name: str
    version: str


class Assumptions(Strict):
    source: Literal["user", "preset", "fitted"]
    label: str | None
    horizontal_speed_m_h: float
    ascent_rate_m_h: float
    descent_rate_m_h: float
    break_s: float
    delay_s: float


class ScenarioInputs(Strict):
    provenance: Literal["calculated"]
    distance_m: float | None
    ascent_m: float | None
    descent_m: float | None


class Breakdown(Strict):
    horizontal: float | int | None
    vertical: float | int | None
    moving: float | int | None
    # "break" is a Python keyword, so the field is aliased.
    break_: float | int | None = Field(alias="break")
    delay: float | int | None

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class DurationScenario(Strict):
    label: str
    speed_multiplier: float
    estimated_duration_s: float | int | None
    breakdown_s: Breakdown


class Observed(Strict):
    provenance: Literal["observed"]
    track_id: int
    name: str | None
    relation: Literal["same track", "linked activity"]
    moving_time_s: float | int | None
    elapsed_time_s: float | int | None
    implied_speed_multiplier: float | None


class PersonalInfo(Strict):
    provenance: Literal["fitted"]
    model: Literal["multiplier", "split"]
    n_activities: int
    n_routes: int
    effort: str
    loo_median_abs_error: float
    range_multipliers: list[float]
    range_meaning: str
    synthetic_data: bool


class Scenarios(Strict):
    format_version: str
    track: StoredRef
    provenance: Literal["estimated"]
    method: Method
    analysis: AnalysisRef
    assumptions: Assumptions
    inputs: ScenarioInputs
    scenarios: list[DurationScenario]
    observed: list[Observed]
    uncertainty_notes: list[str]
    elevation_series: ElevationSeries
    personal: PersonalInfo | None = Field(
        description="what fitted personal speeds rest on (ADR-019)"
    )


# --- DEM elevation (ADR-012) ------------------------------------------------------


class DemDataset(Strict):
    id: str
    name: str
    resolution_m: float
    surface: str
    vertical_datum: str
    attribution: str
    source: Literal["opentopography", "local"] = Field(
        description="local: read from GeoTIFF tiles in the data directory, "
        "never downloaded"
    )
    obtain: str | None = Field(description="where to get tiles for a local dataset")


class DemDatasets(Strict):
    datasets: list[DemDataset]
    downloads_available: bool = Field(
        description="OpenTopography datasets: false when the server has no API "
        "key; then only cached tiles can be used"
    )


class ElevationDrift(Strict):
    """Smoothed recorded-minus-DEM difference (median per 100 m, rolling
    median over window_m)."""

    range_m: float
    start_m: float
    end_m: float
    min_m: float
    min_at_m: float
    max_m: float
    max_at_m: float
    window_m: float


class ElevationDifference(Strict):
    provenance: Literal["calculated"]
    difference: Literal["recorded minus DEM"]
    compared_points: int
    reference_coverage: float | None
    median_difference_m: float | None
    mean_difference_m: float | None
    std_difference_m: float | None
    p95_abs_difference_m: float | None
    max_abs_difference_m: float | None
    max_abs_difference_at_m: float | None
    ascent_recorded_m: float | None
    descent_recorded_m: float | None
    ascent_dem_m: float | None
    descent_dem_m: float | None
    drift: ElevationDrift | None


class SlopeBand(Strict):
    band_deg: str
    distance_m: float | None


class TerrainSlope(Strict):
    """Steepness of the ground around the track from the DEM (ADR-015)."""

    provenance: Literal["calculated"]
    meaning: str
    baseline_m: float
    bands_m: list[SlopeBand]
    unknown_m: float | None
    median_deg: float | None
    p90_deg: float | None
    max_deg: float | None
    max_at_m: float | None


class ElevationCheck(Strict):
    format_version: str
    track: StoredRef
    elevation_series: ElevationSeries
    tiles: list[str]
    downloaded_tiles: list[str]
    reused_stored_profile: bool
    comparison: ElevationDifference
    warnings: list[Warning] = Field(description="e.g. ELEVATION_DRIFT")
    terrain_slope: TerrainSlope | None
    terrain_slope_note: str | None


# --- personal baseline (Phase 7, ADR-016 to ADR-019) ---------------------------------


class BaselineAssumptions(Strict):
    source: Literal["fitted"]
    label: str | None
    horizontal_speed_m_h: float
    ascent_rate_m_h: float
    descent_rate_m_h: float


class BaselineValidation(Strict):
    method: Literal["leave one route out"]
    folds: int
    median_abs_error: float
    max_abs_error: float


class MultiplierModel(Strict):
    status: Literal["fitted", "not enough data", "not identifiable"]
    min_routes: int
    provenance: Literal["fitted"] | None
    speed_multiplier: float | None
    spread_log: float | None
    average_interval_95: list[float] | None
    single_activity_interval_95: list[float] | None
    assumptions: BaselineAssumptions | None
    validation: BaselineValidation | None


class SplitModel(Strict):
    status: Literal["fitted", "not enough data", "not identifiable"]
    reason: str | None
    min_routes: int
    ratio_range: float | None
    min_ratio_range: float
    provenance: Literal["fitted"] | None
    horizontal_factor: float | None
    vertical_factor: float | None
    horizontal_interval_95: list[float] | None
    vertical_interval_95: list[float] | None
    residual_spread_log: float | None
    assumptions: BaselineAssumptions | None
    validation: BaselineValidation | None


class BaselineModels(Strict):
    multiplier: MultiplierModel
    split: SplitModel


class EffortObserved(Strict):
    effort: str
    n_activities: int
    provenance: Literal["observed"]
    mean_implied_multiplier: float


class EffortOutside(Strict):
    track_id: int
    name: str | None
    effort: str | None
    pace_vs_multiplier: float | None
    pace_vs_split: float | None


class BaselineEffort(Strict):
    fitted_on: str
    included: list[str]
    observed_by_effort: list[EffortObserved]
    outside_fit: list[EffortOutside]


class BaselineActivity(Strict):
    track_id: int
    name: str | None
    route: int
    started_at_utc: str | None
    distance_m: float | None
    ascent_m: float | None
    descent_m: float | None
    moving_time_s: float | None
    effort: str | None
    reference_moving_s: float | None
    vertical_horizontal_ratio: float | None
    implied_speed_multiplier: float
    loo_error_multiplier: float | None
    loo_error_split: float | None


class BaselineExcluded(Strict):
    track_id: int
    name: str | None
    reason: str


class BaselineReference(Strict):
    label: str | None
    horizontal_speed_m_h: float
    ascent_rate_m_h: float
    descent_rate_m_h: float


class BaselineRequirements(Strict):
    min_moving_s: float
    activity_types: list[str]


class Baseline(Strict):
    format_version: str
    method: Literal["din33466"]
    elevation: str
    analysis: AnalysisRef
    reference: BaselineReference
    requirements: BaselineRequirements
    n_activities: int
    n_routes: int
    synthetic_data: bool
    synthetic_track_ids: list[int]
    models: BaselineModels
    effort: BaselineEffort
    activities: list[BaselineActivity]
    excluded: list[BaselineExcluded]


# --- explanations (Phase 8, ADR-020) ---------------------------------------------


class ExplanationStatement(Strict):
    text: str
    facts: list[str]
    caveat: bool = Field(description="fixed qualification, never reworded by a model")


class ExplanationFact(Strict):
    key: str
    label: str
    display: str
    provenance: str


class Explanation(Strict):
    format_version: str
    topic: Literal["scenarios", "elevation", "baseline", "comparison"]
    source: Literal["template", "model"]
    model: str | None
    statements: list[ExplanationStatement]
    boundary: str
    fallback_reasons: list[str] = Field(
        description="why a model's answer was not used (template shown instead)"
    )
    notes: list[str]
    facts: list[ExplanationFact]
