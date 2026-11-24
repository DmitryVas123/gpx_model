"""DEM adapter, enrichment use case, migration, and CLI, without network access."""

import io
import json
import sqlite3
import urllib.error
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest

from alpha.application.elevation import (
    DemProfileMissingError,
    elevation_check,
    enrich_all,
    enrich_track,
)
from alpha.application.tracks import (
    Workspace,
    delete_source_of_track,
    import_file,
    show_track,
)
from alpha.cli import EXIT_BAD_PARAMS, EXIT_OK, EXIT_UNAVAILABLE, main
from alpha.cli.config import opentopography_api_key, read_env_file
from alpha.dem import DemDownloadError, DemProvider, DemUnavailableError, opentopography
from alpha.domain.analysis import AnalysisParams
from alpha.persistence import Database
from alpha.persistence.schema import SCHEMA_VERSION
from tests.helpers import (
    LAT0,
    LON0,
    FakeFetch,
    geotiff_for_bounds,
    plane_elevation,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
ROUTE = (FIXTURES / "route_only.gpx").read_bytes()  # around 46.0 N, 8.0 E


@pytest.fixture
def ws(tmp_path: Path) -> Iterator[Workspace]:
    workspace = Workspace(tmp_path)
    yield workspace
    workspace.close()


def provider(
    tmp_path: Path, fetch: FakeFetch, key: str | None = "test-key"
) -> DemProvider:
    return DemProvider(tmp_path / "dem", "COP30", key, fetch=fetch)


# --- provider --------------------------------------------------


def test_sampling_matches_the_raster(tmp_path: Path) -> None:
    lat = LAT0 + np.array([0.0, 0.01, 0.1, 0.2])
    lon = LON0 + np.array([0.0, 0.02, 0.05, 0.2])
    sample = provider(tmp_path, FakeFetch()).sample(lat, lon)
    np.testing.assert_allclose(sample.elevation_m, plane_elevation(lat, lon), atol=0.01)
    assert sample.dataset.id == "COP30"


def test_tiles_are_cached_with_provenance(tmp_path: Path) -> None:
    fetch = FakeFetch()
    lat, lon = np.array([LAT0 + 0.1]), np.array([LON0 + 0.1])
    first = provider(tmp_path, fetch).sample(lat, lon)
    second = provider(tmp_path, fetch, key=None).sample(lat, lon)
    assert len(fetch.calls) == 1
    assert first.tiles[0].downloaded and not second.tiles[0].downloaded
    meta = json.loads((tmp_path / "dem/COP30/N046.00_E0008.00.json").read_text())
    assert meta["dataset"] == "COP30" and "retrieved_at_utc" in meta
    # Only the fixed tile (with margin) is requested, not the track's extent.
    assert fetch.calls[0][1] == pytest.approx((45.998, 46.252, 7.998, 8.252))


def test_missing_tile_without_key(tmp_path: Path) -> None:
    with pytest.raises(DemUnavailableError, match="OPENTOPOGRAPHY_API_KEY"):
        provider(tmp_path, FakeFetch(), key=None).sample(
            np.array([LAT0]), np.array([LON0])
        )


def test_unknown_dataset(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown DEM dataset"):
        DemProvider(tmp_path, "NOPE", None)


def test_download_errors_never_reveal_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "s3cr3t-key"

    def reject(*_args: object, **_kwargs: object) -> None:
        body = io.BytesIO(f"Invalid API key {secret}".encode())
        raise urllib.error.HTTPError("url", 401, "Unauthorized", {}, body)  # type: ignore[arg-type]

    monkeypatch.setattr("urllib.request.urlopen", reject)
    with pytest.raises(DemDownloadError) as excinfo:
        opentopography.fetch_geotiff("COP30", (46, 46.1, 8, 8.1), secret)
    assert secret not in str(excinfo.value)
    assert "rejected the API key" in str(excinfo.value)


def test_non_geotiff_response_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response(io.BytesIO):
        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_: object) -> None:
            return None

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_a, **_k: Response(b"<html>Error: key k3y exceeded</html>"),
    )
    with pytest.raises(DemDownloadError, match="did not return a GeoTIFF") as excinfo:
        opentopography.fetch_geotiff("COP30", (46, 46.1, 8, 8.1), "k3y")
    assert "k3y" not in str(excinfo.value)


# --- use case --------------------------------------------------


def test_enrich_stores_and_reuses_profile(ws: Workspace, tmp_path: Path) -> None:
    track_id = import_file(ws, "route.gpx", ROUTE).tracks[0].id
    fetch = FakeFetch()
    first = enrich_track(ws, track_id, provider(tmp_path, fetch))
    assert not first.reused
    assert first.downloaded_tiles == ("N046.00_E0008.00",)
    assert first.profile.sample_spacing_m == 20.0
    assert first.comparison.compared_points == 3

    again = enrich_track(ws, track_id, provider(tmp_path, FakeFetch(), key=None))
    assert again.reused and again.downloaded_tiles == ()
    np.testing.assert_array_equal(again.profile.elevation_m, first.profile.elevation_m)

    offline = elevation_check(ws, track_id, "COP30")
    assert offline.comparison == first.comparison

    refreshed = enrich_track(ws, track_id, provider(tmp_path, fetch), refresh=True)
    assert not refreshed.reused
    assert len(fetch.calls) == 1  # tile came from the cache


def test_elevation_check_requires_profile(ws: Workspace) -> None:
    track_id = import_file(ws, "route.gpx", ROUTE).tracks[0].id
    with pytest.raises(DemProfileMissingError):
        elevation_check(ws, track_id, "COP30")


def test_deleting_the_track_deletes_its_profile(ws: Workspace, tmp_path: Path) -> None:
    track_id = import_file(ws, "route.gpx", ROUTE).tracks[0].id
    enrich_track(ws, track_id, provider(tmp_path, FakeFetch()))
    delete_source_of_track(ws, track_id, confirm=True)
    with ws.db.transaction() as repo:
        assert repo.get_dem_profile(track_id, "COP30") is None


# --- schema upgrade --------------------------------------------------


def test_version_1_database_is_upgraded_in_place(tmp_path: Path) -> None:
    path = tmp_path / "alpha.db"
    Database(path).close()
    connection = sqlite3.connect(path)
    connection.execute("drop table dem_profiles")
    connection.execute("update meta set value = '1'")
    connection.commit()
    connection.close()

    Database(path).close()
    connection = sqlite3.connect(path)
    version = connection.execute("select value from meta").fetchone()[0]
    tables = {r[0] for r in connection.execute("select name from sqlite_master")}
    connection.close()
    assert version == str(SCHEMA_VERSION)
    assert "dem_profiles" in tables


# --- configuration and CLI --------------------------------------------------


def test_env_file_reader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = tmp_path / ".env"
    env.write_text('# comment\nexport A="1"\nB = two\nbroken line\n', encoding="utf-8")
    assert read_env_file(env) == {"A": "1", "B": "two"}
    assert read_env_file(tmp_path / "missing") == {}

    monkeypatch.delenv("OPENTOPOGRAPHY_API_KEY", raising=False)
    env.write_text("OPENTOPOGRAPHY_API_KEY='from-file'\n", encoding="utf-8")
    assert opentopography_api_key(env) == "from-file"
    monkeypatch.setenv("OPENTOPOGRAPHY_API_KEY", "from-env")
    assert opentopography_api_key(env) == "from-env"


def run(*argv: str) -> tuple[int, str]:
    out = io.StringIO()
    return main(list(argv), out=out), out.getvalue()


def test_cli_enrich(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = str(tmp_path / "data")
    run("--data-dir", data, "import", str(FIXTURES / "route_only.gpx"))

    code, text = run("--data-dir", data, "enrich", "1", "--offline")
    assert code == EXIT_UNAVAILABLE and "not cached" in text

    fetch = FakeFetch()
    monkeypatch.setattr(opentopography, "fetch_geotiff", fetch)
    monkeypatch.setenv("OPENTOPOGRAPHY_API_KEY", "test-key")
    code, text = run("--data-dir", data, "enrich", "1")
    assert code == EXIT_OK
    assert "downloaded tiles N046.00_E0008.00" in text
    assert "Copernicus DEM GLO-30" in text and "Attribution" in text
    assert "test-key" not in text

    code, text = run("--data-dir", data, "enrich", "1", "--offline")
    assert code == EXIT_OK and "stored profile" in text
    assert len(fetch.calls) == 1


# --- analysis on the DEM series ---

DEM = AnalysisParams(elevation="dem:COP30")


def test_show_requires_and_then_uses_the_dem_profile(
    ws: Workspace, tmp_path: Path
) -> None:
    track_id = import_file(ws, "route.gpx", ROUTE).tracks[0].id
    with pytest.raises(DemProfileMissingError, match="alpha enrich"):
        show_track(ws, track_id, DEM)

    enriched = enrich_track(ws, track_id, provider(tmp_path, FakeFetch()))
    view = show_track(ws, track_id, DEM)
    assert view.dem is not None and view.dem.dataset_id == "COP30"
    assert view.analysis.params.elevation == "dem:COP30"
    assert view.analysis.summary.ascent_m == pytest.approx(
        enriched.comparison.ascent_reference_m
    )
    recorded = show_track(ws, track_id)
    assert recorded.dem is None
    assert recorded.analysis.summary.ascent_m == pytest.approx(
        enriched.comparison.ascent_recorded_m
    )
    with ws.db.transaction() as repo:  # stored recorded elevation is untouched
        np.testing.assert_array_equal(
            repo.load_track(track_id).ele_raw_m, recorded.track.ele_raw_m
        )


def test_refresh_drops_analyses_of_the_old_series(
    ws: Workspace, tmp_path: Path
) -> None:
    track_id = import_file(ws, "route.gpx", ROUTE).tracks[0].id
    enrich_track(ws, track_id, provider(tmp_path, FakeFetch()))
    first = show_track(ws, track_id, DEM).analysis.summary.max_elevation_m

    def shifted(
        dataset_id: str, bounds: tuple[float, float, float, float], api_key: str
    ) -> bytes:
        return geotiff_for_bounds(bounds, lambda la, lo: plane_elevation(la, lo) + 100)

    fresh_cache = tmp_path / "other-cache"
    enrich_track(
        ws,
        track_id,
        DemProvider(fresh_cache, "COP30", "k", fetch=shifted),
        refresh=True,
    )
    second = show_track(ws, track_id, DEM).analysis.summary.max_elevation_m
    assert second == pytest.approx(first + 100)  # type: ignore[operator]


def test_compare_with_dem_needs_both_tracks(ws: Workspace, tmp_path: Path) -> None:
    from alpha.application.tracks import compare_tracks

    a = import_file(ws, "route.gpx", ROUTE).tracks[0].id
    b = (
        import_file(
            ws, "activity.gpx", (FIXTURES / "activity_two_segments.gpx").read_bytes()
        )
        .tracks[0]
        .id
    )
    enrich_track(ws, a, provider(tmp_path, FakeFetch()))
    with pytest.raises(DemProfileMissingError) as excinfo:
        compare_tracks(ws, a, b, DEM)
    assert excinfo.value.track_id == b
    enrich_track(ws, b, provider(tmp_path, FakeFetch()))
    c = compare_tracks(ws, a, b, DEM)
    assert c.dem_a is not None and c.dem_b is not None


def test_dem_enriched_cannot_be_set_by_hand(ws: Workspace) -> None:
    from alpha.application.tracks import InvalidEditError, edit_track
    from alpha.domain.track import ElevationSource

    track_id = import_file(ws, "route.gpx", ROUTE).tracks[0].id
    with pytest.raises(InvalidEditError, match="alpha enrich"):
        edit_track(ws, track_id, elevation_source=ElevationSource.DEM_ENRICHED)


def test_cli_and_api_with_dem_elevation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    from alpha.api import create_app

    data = str(tmp_path / "data")
    run("--data-dir", data, "import", str(FIXTURES / "route_only.gpx"))
    code, text = run("--data-dir", data, "show", "1", "--elevation", "dem:COP30")
    assert code == EXIT_UNAVAILABLE and "alpha enrich 1" in text

    monkeypatch.setattr(opentopography, "fetch_geotiff", FakeFetch())
    monkeypatch.setenv("OPENTOPOGRAPHY_API_KEY", "test-key")
    run("--data-dir", data, "enrich", "1")
    code, text = run("--data-dir", data, "show", "1", "--elevation", "dem:COP30")
    assert code == EXIT_OK
    assert "elevation: DEM Copernicus DEM GLO-30" in text
    assert "Attribution: Copernicus DEM GLO-30" in text
    code, text = run(
        "--data-dir", data, "scenarios", "1", "--elevation", "dem:COP30", "--json"
    )
    assert json.loads(text)["elevation_series"]["dataset_id"] == "COP30"

    with TestClient(create_app(tmp_path / "data")) as client:
        ok = client.get("/tracks/1", params={"elevation": "dem:COP30"}).json()
        assert ok["elevation_series"]["source"] == "dem"
        assert "Copernicus" in ok["elevation_series"]["attribution"]
        recorded = client.get("/tracks/1").json()["elevation_series"]
        assert recorded["source"] == "recorded"
        assert recorded["attribution"] is None
        bad = client.get("/tracks/1", params={"elevation": "nope"})
        assert bad.json()["error"]["code"] == "INVALID_PARAMS"
        run("--data-dir", data, "import", str(FIXTURES / "activity_two_segments.gpx"))
        missing = client.get(
            "/comparisons", params={"a": 1, "b": 2, "elevation": "dem:COP30"}
        )
        assert (missing.status_code, missing.json()["error"]["code"]) == (
            409,
            "DEM_PROFILE_MISSING",
        )


# --- batch enrichment, API endpoints, page data ---


def failing_fetch(
    dataset_id: str, bounds: tuple[float, float, float, float], api_key: str
) -> bytes:
    raise DemDownloadError("OpenTopography answered HTTP 401")


def two_tracks(ws: Workspace) -> tuple[int, int]:
    a = import_file(ws, "route.gpx", ROUTE).tracks[0].id
    activity = (FIXTURES / "activity_two_segments.gpx").read_bytes()
    b = import_file(ws, "activity.gpx", activity).tracks[0].id
    return a, b


def test_enrich_all_skips_stored_profiles(ws: Workspace, tmp_path: Path) -> None:
    a, b = two_tracks(ws)
    fetch = FakeFetch()
    enrich_track(ws, a, provider(tmp_path, fetch))
    batch = enrich_all(ws, provider(tmp_path, fetch))
    assert [v.record.id for v in batch.enriched] == [b]
    assert batch.skipped_track_ids == (a,) and not batch.failed and not batch.stopped

    again = enrich_all(ws, provider(tmp_path, fetch))
    assert not again.enriched and again.skipped_track_ids == (a, b)
    refreshed = enrich_all(ws, provider(tmp_path, fetch), refresh=True)
    assert [v.record.id for v in refreshed.enriched] == [a, b]
    assert all(v.reused is False for v in refreshed.enriched)


def test_enrich_all_reports_unavailable_and_stops_on_download_error(
    ws: Workspace, tmp_path: Path
) -> None:
    two_tracks(ws)
    offline = enrich_all(ws, provider(tmp_path, FakeFetch(), key=None))
    assert len(offline.failed) == 2 and not offline.stopped  # each one tried
    assert "OPENTOPOGRAPHY_API_KEY" in offline.failed[0].message

    broken = enrich_all(
        ws, DemProvider(tmp_path / "dem", "COP30", "secret", fetch=failing_fetch)
    )
    assert len(broken.failed) == 1 and broken.stopped  # not repeated per track
    assert "secret" not in broken.failed[0].message


def test_cli_enrich_all_and_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = str(tmp_path / "data")
    run("--data-dir", data, "import", str(FIXTURES / "route_only.gpx"))
    run("--data-dir", data, "import", str(FIXTURES / "activity_two_segments.gpx"))
    fetch = FakeFetch()
    monkeypatch.setattr(opentopography, "fetch_geotiff", fetch)
    monkeypatch.setenv("OPENTOPOGRAPHY_API_KEY", "test-key")

    code, text = run("--data-dir", data, "enrich", "--all")
    assert code == EXIT_OK
    assert "Enriched 2, failed 0, already stored 0" in text
    assert "Attribution: Copernicus DEM GLO-30" in text and "test-key" not in text
    code, text = run("--data-dir", data, "enrich", "--all")
    assert "Enriched 0, failed 0, already stored 2" in text

    code, text = run("--data-dir", data, "list")
    assert code == EXIT_OK and text.count("COP30") == 2

    code, text = run("--data-dir", data, "enrich", "1", "--json")
    report = json.loads(text)
    assert report["reused_stored_profile"] is True
    assert report["comparison"]["difference"] == "recorded minus DEM"
    assert report["elevation_series"]["attribution"].startswith("Copernicus")

    code, _ = run("--data-dir", data, "enrich", "--all", "--json")
    assert code == EXIT_BAD_PARAMS
    with pytest.raises(SystemExit):  # a track ID or --all, not both
        run("--data-dir", data, "enrich", "1", "--all")


def test_api_enrichment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from alpha.api import create_app

    fetch = FakeFetch()
    monkeypatch.setattr(opentopography, "fetch_geotiff", fetch)
    route = {"content": ROUTE, "params": {"filename": "route.gpx"}}
    with TestClient(create_app(tmp_path, dem_api_key="secret-key")) as client:
        info = client.get("/dem/datasets").json()
        assert info["downloads_available"] is True
        assert info["datasets"][0]["id"] == "COP30"
        track_id = client.post("/files", **route).json()["tracks"][0]["id"]

        missing = client.get(f"/tracks/{track_id}/dem/COP30")
        assert (missing.status_code, missing.json()["error"]["code"]) == (
            409,
            "DEM_PROFILE_MISSING",
        )
        offline = client.post(f"/tracks/{track_id}/dem", params={"offline": True})
        assert offline.status_code == 503
        assert offline.json()["error"]["code"] == "DEM_UNAVAILABLE"
        assert not fetch.calls

        created = client.post(f"/tracks/{track_id}/dem")
        assert created.status_code == 200, created.text
        body = created.json()
        assert body["downloaded_tiles"] == ["N046.00_E0008.00"]
        assert body["reused_stored_profile"] is False
        assert "Copernicus" in body["elevation_series"]["attribution"]
        assert body["warnings"] == []  # flat synthetic data: no drift
        assert body["comparison"]["drift"]["window_m"] == 500.0

        check = client.get(f"/tracks/{track_id}/dem/COP30").json()
        assert check["reused_stored_profile"] is True
        assert check["comparison"] == body["comparison"]
        assert client.get("/tracks").json()[0]["dem_datasets"] == ["COP30"]
        assert (
            client.get(
                f"/tracks/{track_id}", params={"elevation": "dem:COP30"}
            ).status_code
            == 200
        )

        unknown = client.post(f"/tracks/{track_id}/dem", params={"dataset": "X"})
        assert unknown.json()["error"]["code"] == "INVALID_PARAMS"
        assert client.get(f"/tracks/{track_id}/dem/X").status_code == 422
        assert client.post("/tracks/999/dem").status_code == 404
        assert len(fetch.calls) == 1
        for response in (info, body, check):
            assert "secret-key" not in json.dumps(response)

    monkeypatch.setattr(opentopography, "fetch_geotiff", failing_fetch)
    with TestClient(create_app(tmp_path / "b", dem_api_key="k")) as client:
        track_id = client.post("/files", **route).json()["tracks"][0]["id"]
        failed = client.post(f"/tracks/{track_id}/dem")
        assert (failed.status_code, failed.json()["error"]["code"]) == (
            502,
            "DEM_DOWNLOAD_FAILED",
        )
    with TestClient(create_app(tmp_path / "c")) as client:  # no key
        assert client.get("/dem/datasets").json()["downloads_available"] is False
        track_id = client.post("/files", **route).json()["tracks"][0]["id"]
        assert client.post(f"/tracks/{track_id}/dem").status_code == 503


# --- terrain slope (ADR-015) ---


def test_enrich_stores_terrain_slope_of_the_plane(
    ws: Workspace, tmp_path: Path
) -> None:
    from pyproj import Geod

    track_id = import_file(ws, "route.gpx", ROUTE).tracks[0].id
    fetch = FakeFetch()
    view = enrich_track(ws, track_id, provider(tmp_path, fetch))
    assert len(fetch.calls) == 1  # probes near the tile corner use its margin
    # plane_elevation: +20 000 m per degree north, +5 000 m per degree east.
    geod = Geod(ellps="WGS84")
    m_per_deg_lat = geod.inv(LON0, LAT0, LON0, LAT0 + 0.01)[2] / 0.01
    m_per_deg_lon = geod.inv(LON0, LAT0, LON0 + 0.01, LAT0)[2] / 0.01
    expected = np.degrees(
        np.arctan(np.hypot(20_000 / m_per_deg_lat, 5_000 / m_per_deg_lon))
    )
    assert view.slope is not None and view.slope_note is None
    assert view.slope.median_deg == pytest.approx(expected, abs=0.05)
    assert view.slope.max_deg == pytest.approx(expected, abs=0.05)
    assert sum(b.distance_m for b in view.slope.bands if b.label == "10-20") > 0

    with ws.db.transaction() as repo:
        stored = repo.get_terrain_slope(track_id, "COP30")
    assert stored is not None and stored.baseline_m == 15.0
    assert len(stored.slope_deg) == view.record.point_count


def test_slope_is_added_to_an_existing_profile_offline(
    ws: Workspace, tmp_path: Path
) -> None:
    track_id = import_file(ws, "route.gpx", ROUTE).tracks[0].id
    enrich_track(ws, track_id, provider(tmp_path, FakeFetch()))
    with ws.db.transaction() as repo:  # as before schema 3: profile without slope
        repo.delete_terrain_slope(track_id, "COP30")
    check = elevation_check(ws, track_id, "COP30")
    assert check.slope is None and "alpha enrich" in (check.slope_note or "")

    offline = provider(tmp_path, FakeFetch(), key=None)  # tiles are cached
    view = enrich_track(ws, track_id, offline)
    assert view.reused and view.slope is not None
    assert elevation_check(ws, track_id, "COP30").slope is not None

    with ws.db.transaction() as repo:
        repo.delete_terrain_slope(track_id, "COP30")
    nothing = DemProvider(tmp_path / "empty", "COP30", None, fetch=FakeFetch())
    missing = enrich_track(ws, track_id, nothing)  # profile kept, slope unknown
    assert missing.slope is None and "not computed" in (missing.slope_note or "")

    delete_source_of_track(ws, track_id, confirm=True)
    with ws.db.transaction() as repo:
        assert repo.get_terrain_slope(track_id, "COP30") is None


def test_version_2_database_gains_the_slope_table(tmp_path: Path) -> None:
    path = tmp_path / "alpha.db"
    Database(path).close()
    connection = sqlite3.connect(path)
    connection.execute("drop table terrain_slopes")
    connection.execute("update meta set value = '2'")
    connection.commit()
    connection.close()

    Database(path).close()
    connection = sqlite3.connect(path)
    version = connection.execute("select value from meta").fetchone()[0]
    tables = {r[0] for r in connection.execute("select name from sqlite_master")}
    connection.close()
    assert version == str(SCHEMA_VERSION) and "terrain_slopes" in tables


def test_api_reports_terrain_slope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    from alpha.api import create_app

    monkeypatch.setattr(opentopography, "fetch_geotiff", FakeFetch())
    with TestClient(create_app(tmp_path, dem_api_key="k")) as client:
        client.post("/files", params={"filename": "r.gpx"}, content=ROUTE)
        body = client.post("/tracks/1/dem").json()
        slope = body["terrain_slope"]
        assert slope["provenance"] == "calculated"
        assert "not the track gradient" in slope["meaning"]
        assert slope["bands_m"][0]["band_deg"] == "<10"
        assert body["terrain_slope_note"] is None
