"""Use cases for stored tracks (ARCHITECTURE.md §6).

Each use case owns its transaction. Analyses are cached per
(track, algorithm version, params hash) and computed on demand when missing.
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path

from alpha.domain.analysis import (
    ALGORITHM_VERSION,
    Analysis,
    AnalysisParams,
    TrackSummary,
    analyse,
    params_hash,
)
from alpha.domain.comparison import ComparisonResult, compare
from alpha.domain.track import ElevationSource, Track, TrackKind
from alpha.gpx.reader import PARSER_NAME, PARSER_VERSION, read_gpx
from alpha.persistence import (
    Database,
    FileStore,
    NewSourceFile,
    NewTrack,
    Repository,
    SourceFileRecord,
    TrackRecord,
)

DATABASE_FILENAME = "alpha.db"


class TrackNotFoundError(Exception):
    def __init__(self, track_id: int) -> None:
        super().__init__(f"no track with id {track_id}")
        self.track_id = track_id


class InvalidEditError(Exception):
    pass


class Workspace:
    """A data directory: ``alpha.db`` plus ``files/`` with the original GPX files."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.db = Database(root / DATABASE_FILENAME)
        self.files = FileStore(root)

    def close(self) -> None:
        self.db.close()


# --- import ---------------------------------------------------------------------


@dataclass(frozen=True)
class ImportOutcome:
    source: SourceFileRecord
    created: bool  # False: identical bytes were already imported
    tracks: tuple[TrackRecord, ...]


def import_file(
    ws: Workspace, file_name: str, data: bytes, params: AnalysisParams | None = None
) -> ImportOutcome:
    """Import GPX bytes. Idempotent: the same bytes are stored only once.

    Raises ``GpxReadError`` before anything is written if the file is unreadable.
    The original is copied to the file store first; if the database transaction
    then fails, a newly written copy is removed again.
    """
    sha = hashlib.sha256(data).hexdigest()
    with ws.db.transaction() as repo:
        existing = repo.find_source_file(sha)
        if existing is not None:
            return ImportOutcome(existing, False, _records(repo, existing.track_ids))

    doc = read_gpx(data)
    stored_path, written = ws.files.put(sha, data)
    try:
        with ws.db.transaction() as repo:
            source = repo.add_source_file(
                NewSourceFile(
                    sha256=sha,
                    original_filename=file_name,
                    stored_path=stored_path,
                    size_bytes=len(data),
                    creator=doc.creator,
                    gpx_version=doc.gpx_version,
                    waypoint_count=doc.waypoint_count,
                    parser_name=PARSER_NAME,
                    parser_version=PARSER_VERSION,
                    warnings=doc.warnings,
                ),
                [
                    NewTrack(
                        str(t.element), t.index_in_element, t.track, t.read_warnings
                    )
                    for t in doc.tracks
                ],
            )
            for track_id, source_track in zip(
                source.track_ids, doc.tracks, strict=True
            ):
                repo.save_analysis(track_id, analyse(source_track.track, params))
            tracks = _records(repo, source.track_ids)
    except BaseException:
        if written:
            ws.files.delete(stored_path)
        raise
    return ImportOutcome(source, True, tracks)


def _records(repo: Repository, track_ids: tuple[int, ...]) -> tuple[TrackRecord, ...]:
    return tuple(_require(repo, track_id) for track_id in track_ids)


def _require(repo: Repository, track_id: int) -> TrackRecord:
    record = repo.get_track(track_id)
    if record is None:
        raise TrackNotFoundError(track_id)
    return record


# --- reading ----------------------------------------------------------------------


def _analysis(
    repo: Repository, track_id: int, params: AnalysisParams
) -> tuple[Track, Analysis]:
    track = repo.load_track(track_id)
    cached = repo.get_analysis(track_id, ALGORITHM_VERSION, params_hash(params))
    if cached is not None:
        return track, cached
    analysis = analyse(track, params)
    repo.save_analysis(track_id, analysis)
    return track, analysis


@dataclass(frozen=True)
class TrackListItem:
    record: TrackRecord
    summary: TrackSummary


def list_tracks(
    ws: Workspace, params: AnalysisParams | None = None
) -> list[TrackListItem]:
    """All tracks with their summary; missing analyses are computed and cached."""
    p = params or AnalysisParams()
    with ws.db.transaction() as repo:
        summaries = repo.summaries(ALGORITHM_VERSION, params_hash(p))
        items = []
        for record in repo.list_tracks():
            summary = summaries.get(record.id)
            if summary is None:
                summary = _analysis(repo, record.id, p)[1].summary
            items.append(TrackListItem(record, summary))
    return items


@dataclass(frozen=True)
class TrackView:
    record: TrackRecord
    track: Track
    analysis: Analysis


def show_track(
    ws: Workspace, track_id: int, params: AnalysisParams | None = None
) -> TrackView:
    with ws.db.transaction() as repo:
        record = _require(repo, track_id)
        track, analysis = _analysis(repo, track_id, params or AnalysisParams())
    return TrackView(record, track, analysis)


@dataclass(frozen=True)
class TrackComparison:
    a: TrackRecord
    b: TrackRecord
    result: ComparisonResult


def compare_tracks(
    ws: Workspace, track_a: int, track_b: int, params: AnalysisParams | None = None
) -> TrackComparison:
    """Compare B against A under one parameter set (both analysed alike)."""
    p = params or AnalysisParams()
    with ws.db.transaction() as repo:
        record_a, record_b = _require(repo, track_a), _require(repo, track_b)
        t_a, a_a = _analysis(repo, track_a, p)
        t_b, a_b = _analysis(repo, track_b, p)
    return TrackComparison(record_a, record_b, compare(t_a, a_a, t_b, a_b))


# --- editing ------------------------------------------------------------------------


def edit_track(
    ws: Workspace,
    track_id: int,
    *,
    kind: TrackKind | None = None,
    elevation_source: ElevationSource | None = None,
    planned_track_id: int | None = None,
    clear_planned_track: bool = False,
) -> TrackRecord:
    """Record user corrections. Values set here are marked ``inferred_by=user``.

    Linking requires this track to be an activity and the target a planned
    route (after applying ``kind``). Cached analyses are dropped when kind or
    elevation source change, because their warnings mention the old values.
    """
    if planned_track_id is not None and clear_planned_track:
        raise InvalidEditError("cannot set and clear the planned track at once")
    with ws.db.transaction() as repo:
        record = _require(repo, track_id)
        new_kind = kind or record.kind
        if planned_track_id is not None:
            if planned_track_id == track_id:
                raise InvalidEditError("a track cannot be linked to itself")
            target = _require(repo, planned_track_id)
            if target.kind is not TrackKind.PLANNED:
                raise InvalidEditError(
                    f"track {planned_track_id} is '{target.kind}', not 'planned'; "
                    "set its kind first"
                )
            if new_kind is not TrackKind.ACTIVITY:
                raise InvalidEditError(
                    f"track {track_id} is '{new_kind}'; only activities can be "
                    "linked to a planned route"
                )
        if kind is TrackKind.PLANNED and record.planned_track_id is not None:
            clear_planned_track = True  # a plan does not follow another plan
        repo.update_track(
            track_id,
            kind=kind,
            elevation_source=elevation_source,
            planned_track_id=planned_track_id,
            clear_planned_track=clear_planned_track,
        )
        if kind is not None or elevation_source is not None:
            repo.delete_analyses(track_id)
        return _require(repo, track_id)


# --- deleting -----------------------------------------------------------------------


@dataclass(frozen=True)
class DeletionPlan:
    source_file_id: int
    source_filename: str
    stored_path: str
    tracks: tuple[TrackRecord, ...]
    deleted: bool


def delete_source_of_track(
    ws: Workspace, track_id: int, *, confirm: bool
) -> DeletionPlan:
    """Delete the whole source file that contains ``track_id``: every track from
    it, their points and analyses, and the stored GPX copy. Links from other
    activities to these tracks are cleared. Without ``confirm`` nothing is
    deleted and the plan is returned for display."""
    with ws.db.transaction() as repo:
        record = _require(repo, track_id)
        source = repo.get_source_file(record.source_file_id)
        plan = DeletionPlan(
            source_file_id=source.id,
            source_filename=source.original_filename,
            stored_path=source.stored_path,
            tracks=_records(repo, source.track_ids),
            deleted=confirm,
        )
        if confirm:
            repo.delete_source_file(record.source_file_id)
    if confirm:
        ws.files.delete(plan.stored_path)
    return plan
