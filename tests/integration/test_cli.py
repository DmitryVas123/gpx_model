import io
import json
from pathlib import Path

from alpha.cli import (
    EXIT_BAD_PARAMS,
    EXIT_INVALID,
    EXIT_NOT_FOUND,
    EXIT_OK,
    EXIT_READ_ERROR,
    main,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def run(*argv: str) -> tuple[int, str]:
    out = io.StringIO()
    code = main(list(argv), out=out)
    return code, out.getvalue()


def test_inspect_activity() -> None:
    code, text = run("inspect", str(FIXTURES / "activity_two_segments.gpx"))
    assert code == EXIT_OK
    assert 'Track trk[0]  "Synthetic activity"  type: hiking' in text
    assert "points: 8   source segments: 2" in text
    assert "kind: activity (heuristic)" in text
    for expected in ("RECORDING_GAP", "GPS_JUMP_DETECTED", "SOURCE_SEGMENT_GAP"):
        assert expected in text


def test_inspect_route_with_non_ascii_name() -> None:
    code, text = run("inspect", str(FIXTURES / "route_only.gpx"))
    assert code == EXIT_OK
    assert "Маршрут к хижине" in text
    assert "elevation source: planner_dem (heuristic)" in text
    assert "waypoints: 1" in text


def test_inspect_unreadable_file(tmp_path: Path) -> None:
    bad = tmp_path / "bad.gpx"
    bad.write_bytes(b"not xml")
    code, text = run("inspect", str(bad))
    assert code == EXIT_READ_ERROR
    assert "INVALID_XML" in text


def test_inspect_missing_file(tmp_path: Path) -> None:
    code, text = run("inspect", str(tmp_path / "nope.gpx"))
    assert code == EXIT_READ_ERROR
    assert "cannot read" in text


def test_inspect_never_prints_coordinates() -> None:
    _, text = run("inspect", str(FIXTURES / "activity_two_segments.gpx"))
    assert "46.0" not in text


# --- analyse -------------------------------------------------------------------


def test_analyse_text() -> None:
    code, text = run("analyse", str(FIXTURES / "activity_two_segments.gpx"))
    assert code == EXIT_OK
    for expected in (
        "Distance",
        "Ascent / descent",
        "Moving time",
        "Gradient distribution (50 m bins)",
        "Climb sections",
        "SOURCE_SEGMENT_GAP",
    ):
        assert expected in text
    assert "46.0" not in text


def test_analyse_planned_route_shows_unknown_time() -> None:
    code, text = run("analyse", str(FIXTURES / "route_only.gpx"))
    assert code == EXIT_OK
    assert "Moving time       unknown" in text


def test_analyse_json() -> None:
    code, text = run("analyse", "--json", str(FIXTURES / "activity_two_segments.gpx"))
    assert code == EXIT_OK
    data = json.loads(text)
    (track,) = data["tracks"]
    assert data["format_version"] == "6"
    assert track["source"] == {"element": "trk", "index": 0}
    assert track["summary"]["distance_m"]["unit"] == "m"
    assert track["analysis"]["params"]["profile_bin_m"] == 50.0


def test_analyse_params_change_hash() -> None:
    path = str(FIXTURES / "activity_two_segments.gpx")
    _, default = run("analyse", "--json", path)
    _, custom = run("analyse", "--json", "--profile-bin-m", "25", path)
    hash_default = json.loads(default)["tracks"][0]["analysis"]["params_hash"]
    hash_custom = json.loads(custom)["tracks"][0]["analysis"]["params_hash"]
    assert hash_default != hash_custom


def test_analyse_rejects_invalid_params() -> None:
    code, text = run(
        "analyse", "--profile-bin-m", "0", str(FIXTURES / "route_only.gpx")
    )
    assert code == EXIT_BAD_PARAMS
    assert "profile_bin_m" in text


# --- stored tracks ---------------------------------------------------------------


def run_in(data_dir: Path, *argv: str) -> tuple[int, str]:
    return run("--data-dir", str(data_dir), *argv)


def test_import_list_show_compare(tmp_path: Path) -> None:
    code, text = run_in(
        tmp_path,
        "import",
        str(FIXTURES / "route_only.gpx"),
        str(FIXTURES / "activity_two_segments.gpx"),
    )
    assert code == EXIT_OK
    assert text.count(": imported") == 2

    code, text = run_in(tmp_path, "import", str(FIXTURES / "route_only.gpx"))
    assert "already imported" in text

    code, text = run_in(tmp_path, "list")
    assert code == EXIT_OK
    assert "Маршрут к хижине" in text
    assert "Synthetic activity" in text
    assert "? = kind guessed" in text

    code, text = run_in(tmp_path, "show", "2")
    assert code == EXIT_OK
    assert "Moving time" in text and "SOURCE_SEGMENT_GAP" in text

    code, text = run_in(tmp_path, "compare", "1", "2")
    assert code == EXIT_OK
    assert "Moving time" in text and "unknown" in text
    assert "nothing about conditions" in text

    code, text = run_in(tmp_path, "compare", "1", "2", "--json")
    data = json.loads(text)
    assert data["a"]["id"] == 1 and data["b"]["id"] == 2
    assert (
        next(m for m in data["metrics"] if m["metric"] == "moving_time_s")["a"] is None
    )


def test_import_reports_unreadable_files(tmp_path: Path) -> None:
    bad = tmp_path / "bad.gpx"
    bad.write_bytes(b"nope")
    code, text = run_in(
        tmp_path / "data", "import", str(bad), str(FIXTURES / "route_only.gpx")
    )
    assert code == EXIT_READ_ERROR
    assert "INVALID_XML" in text
    assert "route_only.gpx: imported" in text


def test_edit_and_link(tmp_path: Path) -> None:
    run_in(
        tmp_path,
        "import",
        str(FIXTURES / "route_only.gpx"),
        str(FIXTURES / "activity_two_segments.gpx"),
    )
    code, text = run_in(tmp_path, "edit", "2", "--planned-track", "1")
    assert code == EXIT_OK and "follows plan #1" in text
    _, text = run_in(tmp_path, "list")
    assert "(plan #1)" in text

    code, text = run_in(tmp_path, "edit", "1", "--planned-track", "2")
    assert code == EXIT_INVALID

    code, text = run_in(tmp_path, "edit", "2", "--elevation-source", "barometric")
    assert code == EXIT_OK and "barometric (user)" in text

    code, _ = run_in(tmp_path, "edit", "2")
    assert code == EXIT_BAD_PARAMS


def test_delete_needs_yes(tmp_path: Path) -> None:
    run_in(tmp_path, "import", str(FIXTURES / "route_only.gpx"))
    code, text = run_in(tmp_path, "delete", "1")
    assert code == EXIT_OK and "Nothing deleted" in text
    _, text = run_in(tmp_path, "list")
    assert "Маршрут к хижине" in text

    code, text = run_in(tmp_path, "delete", "1", "--yes")
    assert "Deleted route_only.gpx" in text
    _, text = run_in(tmp_path, "list")
    assert "No tracks imported yet" in text


def test_unknown_track_id(tmp_path: Path) -> None:
    code, text = run_in(tmp_path, "show", "7")
    assert code == EXIT_NOT_FOUND
    assert "no track with id 7" in text


# --- scenarios (Phase 4) ------------------------------------------------------------


def test_scenarios_text_and_json(tmp_path: Path) -> None:
    run_in(
        tmp_path,
        "import",
        str(FIXTURES / "route_only.gpx"),
        str(FIXTURES / "activity_two_segments.gpx"),
    )
    run_in(tmp_path, "edit", "2", "--planned-track", "1")

    code, text = run_in(tmp_path, "scenarios", "1", "--break-min", "30")
    assert code == EXIT_OK
    assert "ESTIMATES" in text
    assert "preset: DIN 33466 standard" in text
    assert "breaks 0:30 h" in text
    assert "baseline" in text and "faster" in text and "slower" in text
    assert "Observed (recorded activity times, not estimates)" in text
    assert "linked activity" in text

    code, text = run_in(
        tmp_path, "scenarios", "1", "--horizontal-speed-km-h", "5", "--json"
    )
    data = json.loads(text)
    assert data["provenance"] == "estimated"
    assert data["assumptions"]["source"] == "user"
    assert data["assumptions"]["horizontal_speed_m_h"] == 5000.0
    assert data["observed"][0]["provenance"] == "observed"
    assert [s["label"] for s in data["scenarios"]] == ["faster", "baseline", "slower"]


def test_scenarios_rejects_bad_input(tmp_path: Path) -> None:
    run_in(tmp_path, "import", str(FIXTURES / "route_only.gpx"))
    code, _ = run_in(tmp_path, "scenarios", "1", "--ascent-rate-m-h", "0")
    assert code == EXIT_BAD_PARAMS
    code, _ = run_in(tmp_path, "scenarios", "1", "--speed-multipliers", "1,-2")
    assert code == EXIT_BAD_PARAMS
    code, _ = run_in(tmp_path, "scenarios", "9")
    assert code == EXIT_NOT_FOUND


def test_scenarios_need_elevation(tmp_path: Path) -> None:
    no_ele = tmp_path / "no_ele.gpx"
    no_ele.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<gpx version="1.1" creator="t" xmlns="http://www.topografix.com/GPX/1/1">'
        '<rte><rtept lat="46.0" lon="8.0"/><rtept lat="46.01" lon="8.0"/></rte></gpx>',
        encoding="utf-8",
    )
    run_in(tmp_path / "data", "import", str(no_ele))
    code, text = run_in(tmp_path / "data", "scenarios", "1")
    assert code == EXIT_INVALID
    assert "ascent and descent are unknown" in text
