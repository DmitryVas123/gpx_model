"""Repository tests against a real temporary SQLite database."""

from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import text

from alpha.domain.analysis import AnalysisParams, analyse
from alpha.domain.track import Inference, Track, TrackKind
from alpha.persistence import (
    Database,
    FileStore,
    NewSourceFile,
    NewTrack,
    SchemaMismatchError,
)
from alpha.report import to_json, track_report
from tests.helpers import LAT0, line_track, make_track


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    database = Database(tmp_path / "alpha.db")
    yield database
    database.close()


def source(sha: str = "a" * 64) -> NewSourceFile:
    return NewSourceFile(
        sha256=sha,
        original_filename="x.gpx",
        stored_path=f"files/{sha}.gpx",
        size_bytes=10,
        creator="tests",
        gpx_version="1.1",
        waypoint_count=0,
        parser_name="gpxpy",
        parser_version="0",
        warnings=(),
    )


def activity() -> Track:
    track = line_track(5.0, 1000.0, lambda d: 1000 + 0.1 * d, seconds_per_point=4.0)
    ele = track.ele_raw_m.copy()
    ele[3] = np.nan  # a missing value must survive the round trip as NaN
    return make_track(
        track.lat_deg, ele=ele, time=track.time_utc_s, kind=TrackKind.ACTIVITY
    )


def add(db: Database, track: Track, sha: str = "a" * 64) -> int:
    with db.transaction() as repo:
        record = repo.add_source_file(source(sha), [NewTrack("trk", 0, track, ())])
    return record.track_ids[0]


def test_track_round_trip(db: Database) -> None:
    original = activity()
    track_id = add(db, original)
    with db.transaction() as repo:
        loaded = repo.load_track(track_id)
        record = repo.get_track(track_id)
    for column in ("lat_deg", "lon_deg", "ele_raw_m", "time_utc_s", "source_segment"):
        np.testing.assert_array_equal(
            getattr(loaded, column), getattr(original, column)
        )
    assert loaded.kind is TrackKind.ACTIVITY
    assert record is not None
    assert record.point_count == original.point_count
    assert record.started_at_utc is not None
    assert record.started_at_utc.tzinfo is not None


def test_analysis_round_trip_is_identical(db: Database) -> None:
    track = activity()
    track_id = add(db, track)
    analysis = analyse(track)
    with db.transaction() as repo:
        repo.save_analysis(track_id, analysis)
    with db.transaction() as repo:
        loaded = repo.get_analysis(
            track_id, analysis.algorithm_version, analysis.params_hash
        )
    assert loaded is not None
    assert loaded.params == analysis.params
    assert to_json(track_report(track, loaded)) == to_json(
        track_report(track, analysis)
    )


def test_save_analysis_is_idempotent_and_keyed_by_params(db: Database) -> None:
    track = activity()
    track_id = add(db, track)
    default, custom = analyse(track), analyse(track, AnalysisParams(profile_bin_m=25))
    with db.transaction() as repo:
        repo.save_analysis(track_id, default)
        repo.save_analysis(track_id, default)
        repo.save_analysis(track_id, custom)
        count = repo.session.execute(text("select count(*) from analyses")).scalar()
    assert count == 2


def test_rollback_on_error(db: Database) -> None:
    with pytest.raises(RuntimeError), db.transaction() as repo:
        repo.add_source_file(source(), [NewTrack("trk", 0, activity(), ())])
        raise RuntimeError("boom")
    with db.transaction() as repo:
        assert repo.list_tracks() == []


def test_delete_cascades_and_clears_links(db: Database) -> None:
    plan_id = add(db, make_track([LAT0, LAT0 + 0.001]), sha="p" * 64)
    act_id = add(db, activity(), sha="a" * 64)
    with db.transaction() as repo:
        repo.update_track(act_id, planned_track_id=plan_id)
        repo.save_analysis(plan_id, analyse(repo.load_track(plan_id)))
    with db.transaction() as repo:
        plan = repo.get_track(plan_id)
        assert plan is not None
        repo.delete_source_file(plan.source_file_id)
    with db.transaction() as repo:
        remaining = repo.get_track(act_id)
        counts = {
            table: repo.session.execute(text(f"select count(*) from {table}")).scalar()
            for table in ("tracks", "track_points", "analyses", "profile_bins")
        }
    assert remaining is not None and remaining.planned_track_id is None
    assert counts["tracks"] == 1
    assert counts["analyses"] == 0
    assert counts["profile_bins"] == 0
    assert counts["track_points"] == activity().point_count


def test_update_marks_user_inference(db: Database) -> None:
    track_id = add(
        db, make_track([LAT0, LAT0 + 0.001], kind_inferred_by=Inference.HEURISTIC)
    )
    with db.transaction() as repo:
        repo.update_track(track_id, kind=TrackKind.PLANNED)
        record = repo.get_track(track_id)
    assert record is not None
    assert (record.kind, record.kind_inferred_by) == (TrackKind.PLANNED, Inference.USER)


def test_schema_version_mismatch_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "alpha.db"
    Database(path).close()
    database = Database(path)
    with database.transaction() as repo:
        repo.session.execute(text("update meta set value = '999'"))
    database.close()
    with pytest.raises(SchemaMismatchError, match="schema version 999"):
        Database(path)


def test_file_store(tmp_path: Path) -> None:
    store = FileStore(tmp_path)
    path, written = store.put("ab" * 32, b"<gpx/>")
    assert written
    assert store.put("ab" * 32, b"<gpx/>") == (path, False)
    assert store.read(path) == b"<gpx/>"
    store.delete(path)
    assert not (tmp_path / path).exists()
