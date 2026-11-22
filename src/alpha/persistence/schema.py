"""SQLite schema (DATA_MODEL.md §10, ADR-002). The only module defining tables."""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Bump when tables change. Purely additive changes (new tables only) are
# migrated automatically; anything else is rejected with an explanation
# (ADR-002). 2: dem_profiles (ADR-012).
SCHEMA_VERSION = 3
# Upgrades that only add tables: version -> tables new in version + 1.
ADDITIVE_UPGRADES: dict[int, tuple[str, ...]] = {
    1: ("dem_profiles",),
    2: ("terrain_slopes",),
}


class Base(DeclarativeBase):
    pass


class MetaRow(Base):
    __tablename__ = "meta"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(String)


class SourceFileRow(Base):
    __tablename__ = "source_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    original_filename: Mapped[str] = mapped_column(String)
    stored_path: Mapped[str] = mapped_column(String)  # relative to the data dir
    size_bytes: Mapped[int] = mapped_column(Integer)
    creator: Mapped[str | None] = mapped_column(String, nullable=True)
    gpx_version: Mapped[str | None] = mapped_column(String, nullable=True)
    waypoint_count: Mapped[int] = mapped_column(Integer)
    parser_name: Mapped[str] = mapped_column(String)
    parser_version: Mapped[str] = mapped_column(String)
    warnings_json: Mapped[str] = mapped_column(Text)  # file-level read warnings
    imported_at_utc: Mapped[datetime] = mapped_column(DateTime)


class TrackRow(Base):
    __tablename__ = "tracks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_file_id: Mapped[int] = mapped_column(
        ForeignKey("source_files.id", ondelete="CASCADE"), index=True
    )
    gpx_element: Mapped[str] = mapped_column(String)  # "trk" | "rte"
    index_in_element: Mapped[int] = mapped_column(Integer)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    activity_type: Mapped[str | None] = mapped_column(String, nullable=True)
    kind: Mapped[str] = mapped_column(String)
    kind_inferred_by: Mapped[str] = mapped_column(String)
    elevation_source: Mapped[str] = mapped_column(String)
    elevation_source_inferred_by: Mapped[str] = mapped_column(String)
    planned_track_id: Mapped[int | None] = mapped_column(
        ForeignKey("tracks.id", ondelete="SET NULL"), nullable=True
    )
    point_count: Mapped[int] = mapped_column(Integer)
    source_segment_count: Mapped[int] = mapped_column(Integer)
    has_elevation: Mapped[bool] = mapped_column(Boolean)
    has_timestamps: Mapped[bool] = mapped_column(Boolean)
    started_at_utc: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    read_warnings_json: Mapped[str] = mapped_column(Text)


class TrackPointRow(Base):
    __tablename__ = "track_points"
    __table_args__ = {"sqlite_with_rowid": False}  # noqa: RUF012 (SQLAlchemy API)

    track_id: Mapped[int] = mapped_column(
        ForeignKey("tracks.id", ondelete="CASCADE"), primary_key=True
    )
    idx: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_segment: Mapped[int] = mapped_column(Integer)
    lat_deg: Mapped[float] = mapped_column(Float)
    lon_deg: Mapped[float] = mapped_column(Float)
    ele_raw_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    time_utc_s: Mapped[float | None] = mapped_column(Float, nullable=True)


class AnalysisRow(Base):
    __tablename__ = "analyses"
    __table_args__ = (UniqueConstraint("track_id", "algorithm_version", "params_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    track_id: Mapped[int] = mapped_column(
        ForeignKey("tracks.id", ondelete="CASCADE"), index=True
    )
    algorithm_version: Mapped[str] = mapped_column(String)
    params_hash: Mapped[str] = mapped_column(String(64))
    params_json: Mapped[str] = mapped_column(Text)
    distance_m: Mapped[float] = mapped_column(Float)
    distance_raw_m: Mapped[float] = mapped_column(Float)
    distance_3d_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    ascent_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    descent_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_elevation_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_elevation_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    elapsed_time_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    moving_time_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    stopped_time_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    recording_gap_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    distance_while_stopped_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    gradient_distribution_json: Mapped[str] = mapped_column(Text)
    warnings_json: Mapped[str] = mapped_column(Text)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime)


class ProfileBinRow(Base):
    __tablename__ = "profile_bins"
    __table_args__ = {"sqlite_with_rowid": False}  # noqa: RUF012 (SQLAlchemy API)

    analysis_id: Mapped[int] = mapped_column(
        ForeignKey("analyses.id", ondelete="CASCADE"), primary_key=True
    )
    bin_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    start_distance_m: Mapped[float] = mapped_column(Float)
    end_distance_m: Mapped[float] = mapped_column(Float)
    ele_start_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    ele_end_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    ele_change_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    gradient_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    elapsed_s: Mapped[float | None] = mapped_column(Float, nullable=True)


class ClimbSectionRow(Base):
    __tablename__ = "climb_sections"
    __table_args__ = {"sqlite_with_rowid": False}  # noqa: RUF012 (SQLAlchemy API)

    analysis_id: Mapped[int] = mapped_column(
        ForeignKey("analyses.id", ondelete="CASCADE"), primary_key=True
    )
    section_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    direction: Mapped[str] = mapped_column(String)
    start_distance_m: Mapped[float] = mapped_column(Float)
    end_distance_m: Mapped[float] = mapped_column(Float)
    elevation_change_m: Mapped[float] = mapped_column(Float)
    mean_gradient_percent: Mapped[float] = mapped_column(Float)
    max_bin_gradient_percent: Mapped[float] = mapped_column(Float)
    start_bin: Mapped[int] = mapped_column(Integer)
    end_bin: Mapped[int] = mapped_column(Integer)


class DemProfileRow(Base):
    """A reference elevation series sampled from a DEM, one value per point
    (float64 little-endian; NaN = no data), with its provenance."""

    __tablename__ = "dem_profiles"
    __table_args__ = (UniqueConstraint("track_id", "dataset_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    track_id: Mapped[int] = mapped_column(
        ForeignKey("tracks.id", ondelete="CASCADE"), index=True
    )
    dataset_id: Mapped[str] = mapped_column(String)
    dataset_name: Mapped[str] = mapped_column(String)
    resolution_m: Mapped[float] = mapped_column(Float)
    surface: Mapped[str] = mapped_column(String)
    vertical_datum: Mapped[str] = mapped_column(String)
    attribution: Mapped[str] = mapped_column(Text)
    sample_spacing_m: Mapped[float] = mapped_column(Float)
    tiles_json: Mapped[str] = mapped_column(Text)
    retrieved_at_utc: Mapped[datetime] = mapped_column(DateTime)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime)
    point_count: Mapped[int] = mapped_column(Integer)
    elevation_blob: Mapped[bytes] = mapped_column(LargeBinary)


class TerrainSlopeRow(Base):
    """Terrain slope in degrees, one value per point (float64 little-endian;
    NaN = unknown), derived from the DEM profile of the same dataset
    (ADR-015)."""

    __tablename__ = "terrain_slopes"
    __table_args__ = (UniqueConstraint("track_id", "dataset_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    track_id: Mapped[int] = mapped_column(
        ForeignKey("tracks.id", ondelete="CASCADE"), index=True
    )
    dataset_id: Mapped[str] = mapped_column(String)
    baseline_m: Mapped[float] = mapped_column(Float)
    sample_spacing_m: Mapped[float] = mapped_column(Float)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime)
    point_count: Mapped[int] = mapped_column(Integer)
    slope_blob: Mapped[bytes] = mapped_column(LargeBinary)
