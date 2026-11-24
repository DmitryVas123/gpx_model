"""SQLite access: the only place that runs SQL (ARCHITECTURE.md §5, ADR-002).

``Database.transaction()`` yields a :class:`Repository` bound to one session;
everything done through it is committed together or rolled back together.
"""

import json
import math
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import Select, create_engine, delete, event, insert, inspect, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from alpha.domain.analysis import Analysis, AnalysisParams, TrackSummary
from alpha.domain.climbs import ClimbSection, Direction
from alpha.domain.profile import ProfileBin
from alpha.domain.quality import QualityParams, QualityWarning, WarningCode
from alpha.domain.track import Effort, ElevationSource, Inference, Track, TrackKind
from alpha.persistence.schema import (
    ADDITIVE_UPGRADES,
    SCHEMA_VERSION,
    AnalysisRow,
    Base,
    ClimbSectionRow,
    DemProfileRow,
    MetaRow,
    ProfileBinRow,
    SourceFileRow,
    TerrainSlopeRow,
    TrackAnnotationRow,
    TrackPointRow,
    TrackRow,
)


class SchemaMismatchError(Exception):
    """The database was created by an incompatible version of ALPHA."""


@dataclass(frozen=True)
class NewSourceFile:
    sha256: str
    original_filename: str
    stored_path: str
    size_bytes: int
    creator: str | None
    gpx_version: str | None
    waypoint_count: int
    parser_name: str
    parser_version: str
    warnings: tuple[QualityWarning, ...]


@dataclass(frozen=True)
class NewTrack:
    gpx_element: str
    index_in_element: int
    track: Track
    read_warnings: tuple[QualityWarning, ...]


@dataclass(frozen=True)
class SourceFileRecord:
    id: int
    sha256: str
    original_filename: str
    stored_path: str
    size_bytes: int
    creator: str | None
    imported_at_utc: datetime
    track_ids: tuple[int, ...]


@dataclass(frozen=True)
class TrackRecord:
    """Stored metadata of a track (no points)."""

    id: int
    source_file_id: int
    source_filename: str
    gpx_element: str
    index_in_element: int
    name: str | None
    activity_type: str | None
    kind: TrackKind
    kind_inferred_by: Inference
    elevation_source: ElevationSource
    elevation_source_inferred_by: Inference
    planned_track_id: int | None
    point_count: int
    has_elevation: bool
    has_timestamps: bool
    started_at_utc: datetime | None
    imported_at_utc: datetime
    read_warnings: tuple[QualityWarning, ...]
    effort: Effort | None = None  # user annotation (ADR-018)


@dataclass(frozen=True)
class DemProfileRecord:
    """Stored reference elevation from a DEM, with provenance (ADR-012)."""

    track_id: int
    dataset_id: str
    dataset_name: str
    resolution_m: float
    surface: str
    vertical_datum: str
    attribution: str
    sample_spacing_m: float
    tiles: tuple[str, ...]
    retrieved_at_utc: datetime
    created_at_utc: datetime
    elevation_m: np.ndarray[tuple[int], np.dtype[np.float64]]


@dataclass(frozen=True)
class TerrainSlopeRecord:
    """Stored terrain slope per point, from one DEM dataset (ADR-015)."""

    track_id: int
    dataset_id: str
    baseline_m: float  # central differences over +/- baseline_m
    sample_spacing_m: float
    created_at_utc: datetime
    slope_deg: np.ndarray[tuple[int], np.dtype[np.float64]]


def _enable_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


class Database:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.engine: Engine = create_engine(f"sqlite:///{path.as_posix()}")
        event.listen(self.engine, "connect", _enable_foreign_keys)
        self._check_or_create_schema()

    def _check_or_create_schema(self) -> None:
        if inspect(self.engine).has_table("meta"):
            with Session(self.engine) as session:
                row = session.get(MetaRow, "schema_version")
                found = row.value if row else None
            if found is not None and found.isdigit() and int(found) < SCHEMA_VERSION:
                self._upgrade(int(found))
                return
            if found != str(SCHEMA_VERSION):
                raise SchemaMismatchError(
                    f"{self.path} has schema version {found}; this ALPHA needs "
                    f"{SCHEMA_VERSION}. Move the database aside and re-import the "
                    "GPX files (originals are kept in the files/ directory)."
                )
            return
        Base.metadata.create_all(self.engine)
        with Session(self.engine) as session, session.begin():
            session.add(MetaRow(key="schema_version", value=str(SCHEMA_VERSION)))

    def _upgrade(self, found: int) -> None:
        """Apply additive upgrades (new tables only) from ``found`` onwards."""
        for version in range(found, SCHEMA_VERSION):
            if version not in ADDITIVE_UPGRADES:
                raise SchemaMismatchError(
                    f"{self.path} has schema version {found}; no automatic upgrade "
                    f"to {SCHEMA_VERSION}. Move the database aside and re-import."
                )
        tables = [
            Base.metadata.tables[name]
            for version in range(found, SCHEMA_VERSION)
            for name in ADDITIVE_UPGRADES[version]
        ]
        Base.metadata.create_all(self.engine, tables=tables)
        with Session(self.engine) as session, session.begin():
            row = session.get(MetaRow, "schema_version")
            if row is None:
                raise SchemaMismatchError(f"{self.path} has no schema version")
            row.value = str(SCHEMA_VERSION)

    @contextmanager
    def transaction(self) -> Iterator["Repository"]:
        with Session(self.engine, expire_on_commit=False) as session, session.begin():
            yield Repository(session)

    def close(self) -> None:
        self.engine.dispose()


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)  # stored as naive UTC


def _as_utc(value: datetime | None) -> datetime | None:
    return None if value is None else value.replace(tzinfo=UTC)


def _optional(value: float) -> float | None:
    return None if math.isnan(value) else value


# --- warnings (de)serialisation -------------------------------------------------


def warnings_to_json(warnings: Sequence[QualityWarning]) -> str:
    return json.dumps(
        [
            {
                "code": str(w.code),
                "message": w.message,
                "point_indices": list(w.point_indices),
                "count": w.count,
                "bin_indices": list(w.bin_indices),
            }
            for w in warnings
        ],
        ensure_ascii=False,
    )


def warnings_from_json(text: str) -> tuple[QualityWarning, ...]:
    return tuple(
        QualityWarning(
            code=WarningCode(item["code"]),
            message=item["message"],
            point_indices=tuple(item["point_indices"]),
            count=item["count"],
            bin_indices=tuple(item["bin_indices"]),
        )
        for item in json.loads(text)
    )


def params_from_json(text: str) -> AnalysisParams:
    data = json.loads(text)
    quality = QualityParams(**data.pop("quality"))
    return AnalysisParams(quality=quality, **data)


class Repository:
    def __init__(self, session: Session) -> None:
        self.session = session

    # --- source files and tracks --------------------------------------------------

    def find_source_file(self, sha256: str) -> SourceFileRecord | None:
        row = self.session.scalars(
            select(SourceFileRow).where(SourceFileRow.sha256 == sha256)
        ).one_or_none()
        return None if row is None else self._source_record(row)

    def get_source_file(self, source_file_id: int) -> SourceFileRecord:
        row = self.session.get(SourceFileRow, source_file_id)
        if row is None:
            raise KeyError(source_file_id)
        return self._source_record(row)

    def add_source_file(
        self, source: NewSourceFile, tracks: Sequence[NewTrack]
    ) -> SourceFileRecord:
        row = SourceFileRow(
            sha256=source.sha256,
            original_filename=source.original_filename,
            stored_path=source.stored_path,
            size_bytes=source.size_bytes,
            creator=source.creator,
            gpx_version=source.gpx_version,
            waypoint_count=source.waypoint_count,
            parser_name=source.parser_name,
            parser_version=source.parser_version,
            warnings_json=warnings_to_json(source.warnings),
            imported_at_utc=_now(),
        )
        self.session.add(row)
        self.session.flush()
        for new in tracks:
            self._add_track(row.id, new)
        return self._source_record(row)

    def _add_track(self, source_file_id: int, new: NewTrack) -> int:
        t = new.track
        started = None
        if t.has_timestamps:
            started = datetime.fromtimestamp(float(t.time_utc_s[0]), UTC).replace(
                tzinfo=None
            )
        row = TrackRow(
            source_file_id=source_file_id,
            gpx_element=new.gpx_element,
            index_in_element=new.index_in_element,
            name=t.name,
            activity_type=t.activity_type,
            kind=str(t.kind),
            kind_inferred_by=str(t.kind_inferred_by),
            elevation_source=str(t.elevation_source),
            elevation_source_inferred_by=str(t.elevation_source_inferred_by),
            planned_track_id=None,
            point_count=t.point_count,
            source_segment_count=t.source_segment_count,
            has_elevation=t.has_elevation,
            has_timestamps=t.has_timestamps,
            started_at_utc=started,
            read_warnings_json=warnings_to_json(new.read_warnings),
        )
        self.session.add(row)
        self.session.flush()
        if t.point_count:
            points = [
                {
                    "track_id": row.id,
                    "idx": i,
                    "source_segment": seg,
                    "lat_deg": lat,
                    "lon_deg": lon,
                    "ele_raw_m": _optional(ele),
                    "time_utc_s": _optional(time),
                }
                for i, (seg, lat, lon, ele, time) in enumerate(
                    zip(
                        t.source_segment.tolist(),
                        t.lat_deg.tolist(),
                        t.lon_deg.tolist(),
                        t.ele_raw_m.tolist(),
                        t.time_utc_s.tolist(),
                        strict=True,
                    )
                )
            ]
            self.session.execute(insert(TrackPointRow), points)
        return row.id

    def _track_query(
        self,
    ) -> Select[TrackRow, SourceFileRow, TrackAnnotationRow]:
        return (
            select(TrackRow, SourceFileRow, TrackAnnotationRow)
            .join(SourceFileRow, TrackRow.source_file_id == SourceFileRow.id)
            .outerjoin(TrackAnnotationRow, TrackAnnotationRow.track_id == TrackRow.id)
        )

    def list_tracks(self) -> list[TrackRecord]:
        rows = self.session.execute(self._track_query().order_by(TrackRow.id)).all()
        return [self._track_record(t, s, a) for t, s, a in rows]

    def get_track(self, track_id: int) -> TrackRecord | None:
        row = self.session.execute(
            self._track_query().where(TrackRow.id == track_id)
        ).one_or_none()
        return None if row is None else self._track_record(row[0], row[1], row[2])

    def load_track(self, track_id: int) -> Track:
        row = self.session.get(TrackRow, track_id)
        if row is None:
            raise KeyError(track_id)
        points = self.session.execute(
            select(
                TrackPointRow.source_segment,
                TrackPointRow.lat_deg,
                TrackPointRow.lon_deg,
                TrackPointRow.ele_raw_m,
                TrackPointRow.time_utc_s,
            )
            .where(TrackPointRow.track_id == track_id)
            .order_by(TrackPointRow.idx)
        ).all()
        columns = list(zip(*points, strict=True)) if points else [()] * 5
        return Track(
            name=row.name,
            kind=TrackKind(row.kind),
            kind_inferred_by=Inference(row.kind_inferred_by),
            elevation_source=ElevationSource(row.elevation_source),
            elevation_source_inferred_by=Inference(row.elevation_source_inferred_by),
            activity_type=row.activity_type,
            # None -> NaN for the float columns.
            source_segment=np.array(columns[0], dtype=np.int32),
            lat_deg=np.array(columns[1], dtype=np.float64),
            lon_deg=np.array(columns[2], dtype=np.float64),
            ele_raw_m=np.array(columns[3], dtype=np.float64),
            time_utc_s=np.array(columns[4], dtype=np.float64),
        )

    def update_track(
        self,
        track_id: int,
        *,
        kind: TrackKind | None = None,
        elevation_source: ElevationSource | None = None,
        planned_track_id: int | None = None,
        clear_planned_track: bool = False,
        effort: Effort | None = None,
        clear_effort: bool = False,
    ) -> None:
        row = self.session.get(TrackRow, track_id)
        if row is None:
            raise KeyError(track_id)
        if effort is not None or clear_effort:
            note = self.session.get(TrackAnnotationRow, track_id)
            if note is None:
                note = TrackAnnotationRow(track_id=track_id)
                self.session.add(note)
            note.effort = None if clear_effort else str(effort)
            note.updated_at_utc = _now()
        if kind is not None:
            row.kind = str(kind)
            row.kind_inferred_by = str(Inference.USER)
        if elevation_source is not None:
            row.elevation_source = str(elevation_source)
            row.elevation_source_inferred_by = str(Inference.USER)
        if clear_planned_track:
            row.planned_track_id = None
        elif planned_track_id is not None:
            row.planned_track_id = planned_track_id

    def delete_source_file(self, source_file_id: int) -> None:
        """Delete a source file; tracks, points, and analyses cascade."""
        self.session.execute(
            delete(SourceFileRow).where(SourceFileRow.id == source_file_id)
        )

    # --- analyses ---------------------------------------------------------------

    def get_analysis(
        self, track_id: int, algorithm_version: str, params_hash: str
    ) -> Analysis | None:
        row = self.session.scalars(
            select(AnalysisRow).where(
                AnalysisRow.track_id == track_id,
                AnalysisRow.algorithm_version == algorithm_version,
                AnalysisRow.params_hash == params_hash,
            )
        ).one_or_none()
        return None if row is None else self._analysis(row)

    def summaries(
        self, algorithm_version: str, params_hash: str
    ) -> dict[int, TrackSummary]:
        rows = self.session.scalars(
            select(AnalysisRow).where(
                AnalysisRow.algorithm_version == algorithm_version,
                AnalysisRow.params_hash == params_hash,
            )
        ).all()
        return {row.track_id: self._summary(row) for row in rows}

    def save_analysis(self, track_id: int, analysis: Analysis) -> None:
        """Store an analysis unless one with the same version and params exists."""
        if self.get_analysis(
            track_id, analysis.algorithm_version, analysis.params_hash
        ):
            return
        s = analysis.summary
        row = AnalysisRow(
            track_id=track_id,
            algorithm_version=analysis.algorithm_version,
            params_hash=analysis.params_hash,
            params_json=json.dumps(asdict(analysis.params), sort_keys=True),
            gradient_distribution_json=json.dumps(analysis.gradient_distribution),
            warnings_json=warnings_to_json(analysis.warnings),
            created_at_utc=_now(),
            **{f.name: getattr(s, f.name) for f in fields(TrackSummary)},
        )
        self.session.add(row)
        self.session.flush()
        if analysis.profile:
            self.session.execute(
                insert(ProfileBinRow),
                [
                    {
                        "analysis_id": row.id,
                        "bin_index": b.index,
                        "start_distance_m": b.start_distance_m,
                        "end_distance_m": b.end_distance_m,
                        "ele_start_m": b.ele_start_m,
                        "ele_end_m": b.ele_end_m,
                        "ele_change_m": b.ele_change_m,
                        "gradient_percent": b.gradient_percent,
                        "elapsed_s": b.elapsed_s,
                    }
                    for b in analysis.profile
                ],
            )
        if analysis.climbs:
            self.session.execute(
                insert(ClimbSectionRow),
                [
                    {
                        "analysis_id": row.id,
                        "section_index": i,
                        "direction": str(c.direction),
                        "start_distance_m": c.start_distance_m,
                        "end_distance_m": c.end_distance_m,
                        "elevation_change_m": c.elevation_change_m,
                        "mean_gradient_percent": c.mean_gradient_percent,
                        "max_bin_gradient_percent": c.max_bin_gradient_percent,
                        "start_bin": c.start_bin,
                        "end_bin": c.end_bin,
                    }
                    for i, c in enumerate(analysis.climbs)
                ],
            )

    def delete_analyses(self, track_id: int) -> None:
        self.session.execute(
            delete(AnalysisRow).where(AnalysisRow.track_id == track_id)
        )

    # --- DEM profiles ---------------------------------------------------------------

    def get_dem_profile(
        self, track_id: int, dataset_id: str
    ) -> DemProfileRecord | None:
        row = self.session.scalars(
            select(DemProfileRow).where(
                DemProfileRow.track_id == track_id,
                DemProfileRow.dataset_id == dataset_id,
            )
        ).one_or_none()
        if row is None:
            return None
        values = np.frombuffer(row.elevation_blob, dtype="<f8").astype(np.float64)
        return DemProfileRecord(
            track_id=row.track_id,
            dataset_id=row.dataset_id,
            dataset_name=row.dataset_name,
            resolution_m=row.resolution_m,
            surface=row.surface,
            vertical_datum=row.vertical_datum,
            attribution=row.attribution,
            sample_spacing_m=row.sample_spacing_m,
            tiles=tuple(json.loads(row.tiles_json)),
            retrieved_at_utc=row.retrieved_at_utc.replace(tzinfo=UTC),
            created_at_utc=row.created_at_utc.replace(tzinfo=UTC),
            elevation_m=values,
        )

    def dem_datasets_by_track(self) -> dict[int, tuple[str, ...]]:
        """Dataset IDs with a stored profile, per track (no elevation blobs)."""
        rows = self.session.execute(
            select(DemProfileRow.track_id, DemProfileRow.dataset_id).order_by(
                DemProfileRow.track_id, DemProfileRow.dataset_id
            )
        ).all()
        result: dict[int, tuple[str, ...]] = {}
        for track_id, dataset_id in rows:
            result[track_id] = (*result.get(track_id, ()), dataset_id)
        return result

    def get_terrain_slope(
        self, track_id: int, dataset_id: str
    ) -> TerrainSlopeRecord | None:
        row = self.session.scalars(
            select(TerrainSlopeRow).where(
                TerrainSlopeRow.track_id == track_id,
                TerrainSlopeRow.dataset_id == dataset_id,
            )
        ).one_or_none()
        if row is None:
            return None
        return TerrainSlopeRecord(
            track_id=row.track_id,
            dataset_id=row.dataset_id,
            baseline_m=row.baseline_m,
            sample_spacing_m=row.sample_spacing_m,
            created_at_utc=row.created_at_utc.replace(tzinfo=UTC),
            slope_deg=np.frombuffer(row.slope_blob, dtype="<f8").astype(np.float64),
        )

    def save_terrain_slope(self, slope: TerrainSlopeRecord) -> None:
        """Insert or replace the slope for (track, dataset)."""
        self.delete_terrain_slope(slope.track_id, slope.dataset_id)
        values = np.asarray(slope.slope_deg, dtype="<f8")
        self.session.add(
            TerrainSlopeRow(
                track_id=slope.track_id,
                dataset_id=slope.dataset_id,
                baseline_m=slope.baseline_m,
                sample_spacing_m=slope.sample_spacing_m,
                created_at_utc=slope.created_at_utc.astimezone(UTC).replace(
                    tzinfo=None
                ),
                point_count=len(values),
                slope_blob=values.tobytes(),
            )
        )

    def delete_terrain_slope(self, track_id: int, dataset_id: str) -> None:
        self.session.execute(
            delete(TerrainSlopeRow).where(
                TerrainSlopeRow.track_id == track_id,
                TerrainSlopeRow.dataset_id == dataset_id,
            )
        )

    def save_dem_profile(self, profile: DemProfileRecord) -> None:
        """Insert or replace the profile for (track, dataset)."""
        self.session.execute(
            delete(DemProfileRow).where(
                DemProfileRow.track_id == profile.track_id,
                DemProfileRow.dataset_id == profile.dataset_id,
            )
        )
        values = np.asarray(profile.elevation_m, dtype="<f8")
        self.session.add(
            DemProfileRow(
                track_id=profile.track_id,
                dataset_id=profile.dataset_id,
                dataset_name=profile.dataset_name,
                resolution_m=profile.resolution_m,
                surface=profile.surface,
                vertical_datum=profile.vertical_datum,
                attribution=profile.attribution,
                sample_spacing_m=profile.sample_spacing_m,
                tiles_json=json.dumps(list(profile.tiles)),
                retrieved_at_utc=profile.retrieved_at_utc.astimezone(UTC).replace(
                    tzinfo=None
                ),
                created_at_utc=profile.created_at_utc.astimezone(UTC).replace(
                    tzinfo=None
                ),
                point_count=len(values),
                elevation_blob=values.tobytes(),
            )
        )

    # --- row -> record conversion -------------------------------------------------

    def _source_record(self, row: SourceFileRow) -> SourceFileRecord:
        track_ids = self.session.scalars(
            select(TrackRow.id)
            .where(TrackRow.source_file_id == row.id)
            .order_by(TrackRow.id)
        ).all()
        return SourceFileRecord(
            id=row.id,
            sha256=row.sha256,
            original_filename=row.original_filename,
            stored_path=row.stored_path,
            size_bytes=row.size_bytes,
            creator=row.creator,
            imported_at_utc=row.imported_at_utc.replace(tzinfo=UTC),
            track_ids=tuple(track_ids),
        )

    @staticmethod
    def _track_record(
        row: TrackRow, source: SourceFileRow, note: TrackAnnotationRow | None
    ) -> TrackRecord:
        return TrackRecord(
            id=row.id,
            source_file_id=row.source_file_id,
            source_filename=source.original_filename,
            gpx_element=row.gpx_element,
            index_in_element=row.index_in_element,
            name=row.name,
            activity_type=row.activity_type,
            kind=TrackKind(row.kind),
            kind_inferred_by=Inference(row.kind_inferred_by),
            elevation_source=ElevationSource(row.elevation_source),
            elevation_source_inferred_by=Inference(row.elevation_source_inferred_by),
            planned_track_id=row.planned_track_id,
            point_count=row.point_count,
            has_elevation=row.has_elevation,
            has_timestamps=row.has_timestamps,
            started_at_utc=_as_utc(row.started_at_utc),
            imported_at_utc=source.imported_at_utc.replace(tzinfo=UTC),
            read_warnings=warnings_from_json(row.read_warnings_json),
            effort=Effort(note.effort) if note is not None and note.effort else None,
        )

    @staticmethod
    def _summary(row: AnalysisRow) -> TrackSummary:
        return TrackSummary(
            **{f.name: getattr(row, f.name) for f in fields(TrackSummary)}
        )

    def _analysis(self, row: AnalysisRow) -> Analysis:
        bins = self.session.scalars(
            select(ProfileBinRow)
            .where(ProfileBinRow.analysis_id == row.id)
            .order_by(ProfileBinRow.bin_index)
        ).all()
        climbs = self.session.scalars(
            select(ClimbSectionRow)
            .where(ClimbSectionRow.analysis_id == row.id)
            .order_by(ClimbSectionRow.section_index)
        ).all()
        return Analysis(
            algorithm_version=row.algorithm_version,
            params=params_from_json(row.params_json),
            params_hash=row.params_hash,
            summary=self._summary(row),
            profile=tuple(
                ProfileBin(
                    index=b.bin_index,
                    start_distance_m=b.start_distance_m,
                    end_distance_m=b.end_distance_m,
                    ele_start_m=b.ele_start_m,
                    ele_end_m=b.ele_end_m,
                    ele_change_m=b.ele_change_m,
                    gradient_percent=b.gradient_percent,
                    elapsed_s=b.elapsed_s,
                )
                for b in bins
            ),
            climbs=tuple(
                ClimbSection(
                    direction=Direction(c.direction),
                    start_distance_m=c.start_distance_m,
                    end_distance_m=c.end_distance_m,
                    elevation_change_m=c.elevation_change_m,
                    mean_gradient_percent=c.mean_gradient_percent,
                    max_bin_gradient_percent=c.max_bin_gradient_percent,
                    start_bin=c.start_bin,
                    end_bin=c.end_bin,
                )
                for c in climbs
            ),
            gradient_distribution=json.loads(row.gradient_distribution_json),
            warnings=warnings_from_json(row.warnings_json),
        )
