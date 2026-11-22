"""HTTP API tests (FastAPI TestClient, real SQLite in a temp directory)."""

import json
import logging
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from alpha.api import create_app

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
ROUTE = (FIXTURES / "route_only.gpx").read_bytes()
ACTIVITY = (FIXTURES / "activity_two_segments.gpx").read_bytes()


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(tmp_path)) as test_client:
        yield test_client


def upload(client: TestClient, data: bytes, name: str = "x.gpx") -> int:
    response = client.post("/files", params={"filename": name}, content=data)
    assert response.status_code in (200, 201), response.text
    track_id: int = response.json()["tracks"][0]["id"]
    return track_id


def error_code(response: object) -> str:
    code: str = response.json()["error"]["code"]  # type: ignore[attr-defined]
    return code


# --- meta and UI ------------------------------------------------------------------


def test_health(client: TestClient) -> None:
    assert client.get("/health").json()["status"] == "ok"


def test_ui_is_served_and_self_contained(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    html = response.text
    assert "never says a route is safe" in html
    assert "innerHTML" not in html  # GPX names are untrusted
    assert not re.search(r'(?:src|href)="https?://', html)  # local-first: no CDN


def test_openapi_documents_errors(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    assert "ErrorResponse" in spec["components"]["schemas"]
    track = spec["paths"]["/tracks/{track_id}"]["get"]["responses"]
    assert {"200", "404", "422"} <= set(track)


# --- import ------------------------------------------------------------------------


def test_upload_is_idempotent(client: TestClient) -> None:
    first = client.post("/files", params={"filename": "r.gpx"}, content=ROUTE)
    assert first.status_code == 201
    assert first.json()["created"] is True
    again = client.post("/files", params={"filename": "other.gpx"}, content=ROUTE)
    assert again.status_code == 200
    assert again.json()["created"] is False
    assert again.json()["source_file"]["original_filename"] == "r.gpx"


def test_unreadable_upload(client: TestClient) -> None:
    response = client.post("/files", content=b"not xml")
    assert response.status_code == 422
    assert error_code(response) == "INVALID_XML"
    assert client.get("/tracks").json() == []


def test_too_large_upload(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("alpha.api.app.DEFAULT_MAX_BYTES", 10)
    response = client.post("/files", content=ROUTE)
    assert response.status_code == 413
    assert error_code(response) == "FILE_TOO_LARGE"


# --- tracks ------------------------------------------------------------------------


def test_list_and_show(client: TestClient) -> None:
    route_id = upload(client, ROUTE)
    (item,) = client.get("/tracks").json()
    assert item["record"]["id"] == route_id
    assert item["record"]["kind"] == {"value": "planned", "inferred_by": "heuristic"}
    assert item["summary"]["moving_time_s"] == {
        "value": None,
        "unit": "s",
        "provenance": "unknown",
    }

    detail = client.get(f"/tracks/{route_id}").json()
    assert detail["stored"]["id"] == route_id
    assert detail["analysis"]["params"]["profile_bin_m"] == 50.0
    assert detail["profile"]

    custom = client.get(f"/tracks/{route_id}", params={"profile_bin_m": 25}).json()
    assert custom["analysis"]["params_hash"] != detail["analysis"]["params_hash"]


def test_show_errors(client: TestClient) -> None:
    route_id = upload(client, ROUTE)
    assert error_code(client.get("/tracks/999")) == "TRACK_NOT_FOUND"
    bad = client.get(f"/tracks/{route_id}", params={"profile_bin_m": 0})
    assert (bad.status_code, error_code(bad)) == (422, "INVALID_PARAMS")
    wrong_type = client.get(f"/tracks/{route_id}", params={"profile_bin_m": "abc"})
    assert (wrong_type.status_code, error_code(wrong_type)) == (422, "INVALID_REQUEST")


def test_edit_and_link(client: TestClient) -> None:
    plan = upload(client, ROUTE)
    act = upload(client, ACTIVITY)
    linked = client.patch(f"/tracks/{act}", json={"planned_track_id": plan}).json()
    assert linked["planned_track_id"] == plan

    conflict = client.patch(f"/tracks/{plan}", json={"planned_track_id": act})
    assert (conflict.status_code, error_code(conflict)) == (409, "INVALID_EDIT")

    invalid = client.patch(f"/tracks/{plan}", json={"kind": "boat"})
    assert (invalid.status_code, error_code(invalid)) == (422, "INVALID_REQUEST")
    unknown_field = client.patch(f"/tracks/{plan}", json={"colour": "red"})
    assert unknown_field.status_code == 422

    confirmed = client.patch(f"/tracks/{act}", json={"kind": "activity"}).json()
    assert confirmed["kind"] == {"value": "activity", "inferred_by": "user"}


def test_delete(client: TestClient) -> None:
    route_id = upload(client, ROUTE)
    dry = client.delete(f"/tracks/{route_id}/source-file", params={"dry_run": True})
    assert dry.json()["deleted"] is False
    assert len(client.get("/tracks").json()) == 1
    done = client.delete(f"/tracks/{route_id}/source-file")
    assert done.json()["deleted"] is True
    assert client.get("/tracks").json() == []


# --- comparison and scenarios ------------------------------------------------------


def test_compare(client: TestClient) -> None:
    plan, act = upload(client, ROUTE), upload(client, ACTIVITY)
    c = client.get("/comparisons", params={"a": plan, "b": act}).json()
    moving = next(m for m in c["metrics"] if m["metric"] == "moving_time_s")
    assert moving["a"] is None and moving["b"] is not None
    assert any("nothing about conditions" in n for n in c["notes"])
    missing = client.get("/comparisons", params={"a": plan, "b": 999})
    assert missing.status_code == 404


def test_scenarios(client: TestClient) -> None:
    plan, act = upload(client, ROUTE), upload(client, ACTIVITY)
    client.patch(f"/tracks/{act}", json={"planned_track_id": plan})

    s = client.get(f"/tracks/{plan}/scenarios", params={"break_min": 30}).json()
    assert s["provenance"] == "estimated"
    assert s["assumptions"]["source"] == "preset"
    assert s["assumptions"]["break_s"] == 1800
    assert [x["label"] for x in s["scenarios"]] == ["faster", "baseline", "slower"]
    assert s["scenarios"][1]["breakdown_s"]["break"] == 1800
    assert s["observed"][0]["relation"] == "linked activity"

    own = client.get(
        f"/tracks/{plan}/scenarios",
        params=[
            ("horizontal_speed_km_h", 5),
            ("speed_multiplier", 1),
            ("speed_multiplier", 2),
        ],
    ).json()
    assert own["assumptions"]["source"] == "user"
    assert [x["speed_multiplier"] for x in own["scenarios"]] == [1.0, 2.0]

    bad = client.get(f"/tracks/{plan}/scenarios", params={"speed_multiplier": -1})
    assert error_code(bad) == "INVALID_PARAMS"
    bad = client.get(f"/tracks/{plan}/scenarios", params={"ascent_rate_m_h": 0})
    assert error_code(bad) == "INVALID_PARAMS"


def test_scenarios_need_elevation(client: TestClient) -> None:
    no_ele = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<gpx version="1.1" creator="t" xmlns="http://www.topografix.com/GPX/1/1">'
        b'<rte><rtept lat="46.0" lon="8.0"/><rtept lat="46.01" lon="8.0"/></rte></gpx>'
    )
    track_id = upload(client, no_ele)
    response = client.get(f"/tracks/{track_id}/scenarios")
    assert (response.status_code, error_code(response)) == (422, "SCENARIO_INPUT")


# --- logging and end to end --------------------------------------------------------


def test_request_log_has_no_personal_data(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="alpha.api"):
        response = client.post(
            "/files", params={"filename": "my-secret-hike.gpx"}, content=ROUTE
        )
    assert response.headers["X-Operation-Id"]
    lines = [
        json.loads(r.getMessage()) for r in caplog.records if r.name == "alpha.api"
    ]
    (line,) = lines
    assert line["path"] == "/files" and line["status"] == 201
    text = caplog.text
    assert "my-secret-hike" not in text
    assert "46.0" not in text and "Маршрут" not in text


def test_end_to_end_flow(client: TestClient) -> None:
    """Phase 5 exit criterion: import -> analyse -> link -> compare -> scenarios."""
    plan, act = upload(client, ROUTE, "plan.gpx"), upload(client, ACTIVITY, "act.gpx")
    assert client.get(f"/tracks/{act}").json()["summary"]["moving_time_s"]["value"]
    assert (
        client.patch(f"/tracks/{act}", json={"planned_track_id": plan}).status_code
        == 200
    )
    assert client.get("/comparisons", params={"a": plan, "b": act}).status_code == 200
    scenarios = client.get(f"/tracks/{plan}/scenarios").json()
    assert scenarios["observed"][0]["track_id"] == act
