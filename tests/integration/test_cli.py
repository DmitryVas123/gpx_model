import io
import json
from pathlib import Path

from alpha.cli import EXIT_BAD_PARAMS, EXIT_OK, EXIT_READ_ERROR, main

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
    assert data["format_version"] == "2"
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
