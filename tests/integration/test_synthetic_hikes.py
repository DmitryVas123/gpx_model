"""The synthetic hike generator (scripts/synthetic_hikes.py, ADR-021).

Invented paths only: no personal data, no network. Checks that ALPHA measures
what the generator put in, that the files are labelled, and that synthetic
hikes never reach a personal profile.
"""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from alpha.application.profiles import SYNTHETIC_ACTIVITY_TYPE, open_profile
from alpha.application.tracks import Workspace, analysis_for
from alpha.domain.analysis import AnalysisParams
from alpha.gpx.reader import read_gpx

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "synthetic_hikes.py"


@pytest.fixture(scope="module")
def generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("synthetic_hikes", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["synthetic_hikes"] = module
    spec.loader.exec_module(module)
    return module


def generate(generator: ModuleType, base: Path, *extra: str) -> dict[str, object]:
    args = ["--invented", "--count", "3", "--interval-s", "5", "--data-dir", str(base)]
    assert generator.main([*args, *extra]) == 0
    manifest: dict[str, object] = json.loads(
        (base / "synthetic" / "manifest.json").read_text(encoding="utf-8")
    )
    return manifest


def test_generated_hikes_are_labelled_and_measured_as_generated(
    generator: ModuleType, tmp_path: Path
) -> None:
    manifest = generate(
        generator, tmp_path, "--import-into", "dev", "--hard-share", "0"
    )
    hikes = manifest["hikes"]
    assert isinstance(hikes, list) and len(hikes) == 3

    profile = open_profile(tmp_path, "dev")
    assert profile.synthetic
    ws = Workspace(profile.data_dir, synthetic=True)
    try:
        with ws.db.transaction() as repo:
            records = repo.list_tracks()
            assert len(records) == 3
            for record, truth in zip(records, hikes, strict=True):
                assert record.activity_type == SYNTHETIC_ACTIVITY_TYPE
                assert record.name and record.name.startswith("Synthetic S")
                assert record.started_at_utc and record.started_at_utc.year >= 2030
                s = analysis_for(repo, record.id, AnalysisParams())[1].summary
                # GPS noise adds a little distance; stops are not moving time.
                assert s.distance_m / truth["distance_m"] == pytest.approx(1, abs=0.04)
                assert s.ascent_m is not None
                assert s.ascent_m / truth["ascent_m"] == pytest.approx(1, abs=0.05)
                assert s.moving_time_s is not None
                assert s.moving_time_s / truth["moving_s"] == pytest.approx(1, abs=0.03)
    finally:
        ws.close()


def test_files_carry_the_synthetic_type(generator: ModuleType, tmp_path: Path) -> None:
    generate(generator, tmp_path)
    files = sorted((tmp_path / "synthetic").glob("*.gpx"))
    assert len(files) == 3
    doc = read_gpx(files[0].read_bytes())
    assert doc.tracks[0].track.activity_type == SYNTHETIC_ACTIVITY_TYPE


def test_same_seed_same_files(generator: ModuleType, tmp_path: Path) -> None:
    generate(generator, tmp_path / "a")
    generate(generator, tmp_path / "b")
    for a in sorted((tmp_path / "a" / "synthetic").glob("*.gpx")):
        assert a.read_bytes() == (tmp_path / "b" / "synthetic" / a.name).read_bytes()


def test_personal_profiles_are_refused(generator: ModuleType, tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="personal"):
        generate(generator, tmp_path, "--import-into", "default")
    assert not (tmp_path / "alpha.db").exists()
