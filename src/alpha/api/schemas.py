"""Pydantic schemas of the HTTP API (DATA_MODEL.md §6.1, ADR-011).

Responses are produced by ``alpha.report`` (the single place that turns domain
results into JSON) and validated against these models. ``extra="forbid"``
makes any drift between the two fail in tests instead of silently changing the
documented API.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from alpha.domain.track import ElevationSource, TrackKind


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- shared ---------------------------------------------------------------------


class ErrorBody(Strict):
    code: str = Field(description="machine-readable error code, e.g. TRACK_NOT_FOUND")
    message: str
    details: list[dict[str, Any]] | None = None


class ErrorResponse(Strict):
    error: ErrorBody


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
    imported_at_utc: str | None
    read_warnings: list[Warning]


class TrackListItem(Strict):
    record: TrackRecord
    summary: Summary


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
