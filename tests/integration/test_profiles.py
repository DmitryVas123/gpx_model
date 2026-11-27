"""Local profiles (ADR-021): folders, CLI, API, and synthetic data labels."""

import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from alpha.api import create_app
from alpha.application.baseline import personal_assumptions, personal_baseline
from alpha.application.profiles import (
    DEFAULT_PROFILE,
    SYNTHETIC_ACTIVITY_TYPE,
    ProfileError,
    create_profile,
    list_profiles,
    open_profile,
)
from alpha.application.scenarios import estimate_personal
from alpha.application.tracks import Workspace
from alpha.cli import EXIT_INVALID, EXIT_NOT_FOUND, EXIT_OK, main
from alpha.explain import RewriteError, explain
from alpha.explain.templates import SYNTHETIC_CAVEAT
from alpha.explain.validate import check
from alpha.report import baseline_report, scenario_report, stored_track_ref
from tests.integration.test_baseline import LOOSE, add, hike

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def run(base: Path, *argv: str) -> tuple[int, str]:
    out = io.StringIO()
    code = main(["--data-dir", str(base), *argv], out)
    return code, out.getvalue()


# --- folders ------------------------------------------------------------------


def test_default_profile_is_the_data_directory(tmp_path: Path) -> None:
    profile = open_profile(tmp_path)
    assert profile.name == DEFAULT_PROFILE
    assert profile.data_dir == tmp_path
    assert not profile.synthetic


def test_created_profile_has_its_own_folder_and_shares_tiles(tmp_path: Path) -> None:
    dev = create_profile(tmp_path, "dev", synthetic=True)
    assert dev.data_dir == tmp_path / "profiles" / "dev"
    assert dev.dem_dir == open_profile(tmp_path).dem_dir == tmp_path / "dem"
    assert open_profile(tmp_path, "dev") == dev
    assert [p.name for p in list_profiles(tmp_path)] == ["default", "dev"]


@pytest.mark.parametrize("name", ["Dev", "../x", "a b", "", "x" * 40])
def test_invalid_names_are_rejected(tmp_path: Path, name: str) -> None:
    with pytest.raises(ProfileError):
        create_profile(tmp_path, name, synthetic=False)


def test_unknown_profile_is_an_error_not_an_empty_workspace(tmp_path: Path) -> None:
    with pytest.raises(ProfileError, match="no profile 'typo'"):
        open_profile(tmp_path, "typo")
    assert not (tmp_path / "profiles" / "typo").exists()


def test_profile_cannot_be_created_twice(tmp_path: Path) -> None:
    create_profile(tmp_path, "dev", synthetic=False)
    with pytest.raises(ProfileError, match="already exists"):
        create_profile(tmp_path, "dev", synthetic=True)
    with pytest.raises(ProfileError):
        create_profile(tmp_path, DEFAULT_PROFILE, synthetic=False)


# --- synthetic activities in fits -----------------------------------------------


def _three_synthetic_hikes(ws: Workspace) -> list[int]:
    return [
        add(ws, name, speed, type_=SYNTHETIC_ACTIVITY_TYPE)
        for name, speed in (("s1", 1.0), ("s2", 1.1), ("s3", 1.2))
    ]


def test_personal_profile_never_fits_synthetic_hikes(tmp_path: Path) -> None:
    ws = Workspace(tmp_path)
    try:
        ids = _three_synthetic_hikes(ws)
        view = personal_baseline(ws, baseline_params=LOOSE)
    finally:
        ws.close()
    assert view.fit.n_activities == 0
    assert not view.synthetic_data
    reasons = {e.track_id: e.reason for e in view.excluded}
    assert all(SYNTHETIC_ACTIVITY_TYPE in reasons[i] for i in ids)


def test_synthetic_profile_fits_and_labels_them(tmp_path: Path) -> None:
    ws = Workspace(tmp_path, synthetic=True)
    try:
        ids = _three_synthetic_hikes(ws)
        add(ws, "real", 1.1)  # ordinary hiking type: used as well
        view = personal_baseline(ws, baseline_params=LOOSE)
    finally:
        ws.close()
    assert view.fit.n_activities == 4
    assert view.synthetic_track_ids == tuple(ids)
    report = baseline_report(view)
    assert report["synthetic_data"] is True
    assert report["synthetic_track_ids"] == ids
    assert SYNTHETIC_ACTIVITY_TYPE in report["requirements"]["activity_types"]
    label = personal_assumptions(view).assumptions.label
    assert label is not None and label.endswith("[synthetic data]")


def test_synthetic_profile_without_synthetic_hikes_is_unlabelled(
    tmp_path: Path,
) -> None:
    ws = Workspace(tmp_path, synthetic=True)
    try:
        for name, speed in (("a", 1.0), ("b", 1.1), ("c", 1.2)):
            add(ws, name, speed)
        view = personal_baseline(ws, baseline_params=LOOSE)
    finally:
        ws.close()
    assert not view.synthetic_data
    label = personal_assumptions(view).assumptions.label
    assert label is not None and "synthetic" not in label


def test_explanations_of_synthetic_fits_carry_a_caveat(tmp_path: Path) -> None:
    ws = Workspace(tmp_path, synthetic=True)
    try:
        ids = _three_synthetic_hikes(ws)
        view = personal_baseline(ws, baseline_params=LOOSE)
        sv = estimate_personal(ws, ids[0], baseline_params=LOOSE)
    finally:
        ws.close()
    r = sv.record
    scenarios = scenario_report(
        stored_track_ref(r.id, r.source_filename, r.planned_track_id),
        sv.result,
        sv.observed,
        sv.dem,
        sv.personal,
    )
    assert scenarios["personal"]["synthetic_data"] is True
    assert any("generated test hikes" in n for n in scenarios["uncertainty_notes"])

    sent: list[str] = []

    def rewriter(system: str, user: str) -> str:
        sent.append(system + user)
        raise RewriteError("no model in tests")

    for topic, report in (
        ("baseline", baseline_report(view)),
        ("scenarios", scenarios),
    ):
        e = explain(topic, report)
        caveats = [s.text for s in e.statements if s.caveat]
        assert SYNTHETIC_CAVEAT in caveats, topic
        assert e.facts is not None
        own = [(s.text, s.facts) for s in e.statements]
        assert not check(own, e.facts, " ".join(t for t, _ in own))
        assert explain(topic, report, rewriter).source == "template"
    # Caveats are never sent to the model (ADR-020).
    assert sent and all(SYNTHETIC_CAVEAT not in prompt for prompt in sent)


# --- CLI ----------------------------------------------------------------------


def test_cli_profiles_keep_data_apart(tmp_path: Path) -> None:
    code, text = run(tmp_path, "profile", "create", "dev", "--synthetic")
    assert code == EXIT_OK and "created synthetic profile dev" in text
    code, text = run(tmp_path, "profile", "list")
    assert code == EXIT_OK
    assert [line.split()[:2] for line in text.splitlines()] == [
        ["default", "personal"],
        ["dev", "synthetic"],
    ]

    gpx = tmp_path / "s.gpx"
    gpx.write_bytes(hike("s", 1.0, SYNTHETIC_ACTIVITY_TYPE))
    assert run(tmp_path, "--profile", "dev", "import", str(gpx))[0] == EXIT_OK
    assert (tmp_path / "profiles" / "dev" / "alpha.db").is_file()

    _, dev_list = run(tmp_path, "--profile", "dev", "list")
    _, default_list = run(tmp_path, "list")
    assert "s" in dev_list.split()
    assert "s" not in default_list.split()


def test_cli_unknown_profile_fails(tmp_path: Path) -> None:
    code, text = run(tmp_path, "--profile", "nope", "list")
    assert code == EXIT_NOT_FOUND and "alpha profile create nope" in text
    assert run(tmp_path, "profile", "create", "Bad Name")[0] == EXIT_INVALID


def test_cli_baseline_json_states_synthetic_data(tmp_path: Path) -> None:
    run(tmp_path, "profile", "create", "dev", "--synthetic")
    _, text = run(tmp_path, "--profile", "dev", "baseline", "--json")
    assert json.loads(text)["synthetic_data"] is False


# --- API ----------------------------------------------------------------------


def test_api_switches_profiles(tmp_path: Path) -> None:
    create_profile(tmp_path, "dev", synthetic=True)
    route = (FIXTURES / "route_only.gpx").read_bytes()
    with TestClient(create_app(tmp_path)) as client:
        health = client.get("/health").json()
        assert health["profile"] == "default" and health["synthetic_data"] is False
        client.post("/files", params={"filename": "r.gpx"}, content=route)
        assert len(client.get("/tracks").json()) == 1

        response = client.put("/profile", json={"name": "dev"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["active"] == "dev"
        assert {p["name"]: p["synthetic_data"] for p in body["profiles"]} == {
            "default": False,
            "dev": True,
        }
        assert "data_dir" not in json.dumps(body)  # no folder paths in the API
        assert client.get("/health").json()["synthetic_data"] is True
        assert client.get("/tracks").json() == []

        missing = client.put("/profile", json={"name": "typo"})
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "PROFILE_NOT_FOUND"
        assert client.get("/profiles").json()["active"] == "dev"

        client.put("/profile", json={"name": "default"})
        assert len(client.get("/tracks").json()) == 1


def test_api_starts_in_the_requested_profile(tmp_path: Path) -> None:
    create_profile(tmp_path, "dev", synthetic=True)
    with TestClient(create_app(tmp_path, profile="dev")) as client:
        assert client.get("/health").json()["profile"] == "dev"
