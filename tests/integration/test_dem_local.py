"""Local GeoTIFF terrain models (ADR-013): synthetic projected tiles, no network."""

import io
import json
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import numpy.typing as npt
import pytest
from pyproj import Transformer

from alpha.application.elevation import enrich_all, enrich_track
from alpha.application.tracks import Workspace, import_file, show_track
from alpha.cli import EXIT_OK, EXIT_UNAVAILABLE, main
from alpha.dem import (
    DemProvider,
    DemUnavailableError,
    LocalTileProvider,
    local,
    open_dem,
)
from alpha.domain.analysis import AnalysisParams

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
ROUTE = (FIXTURES / "route_only.gpx").read_bytes()  # around 46.0 N, 8.0 E
CRS = "EPSG:25832"
TILE_M = 1000.0
PIXEL_M = 10.0
TO_UTM = Transformer.from_crs("EPSG:4326", CRS, always_xy=True)

FloatArray = npt.NDArray[np.float64]


def terrain(x: FloatArray, y: FloatArray) -> FloatArray:
    """A tilted plane in projected metres: bilinear sampling reproduces it."""
    return 400.0 + 0.05 * (x - 400_000.0) + 0.02 * (y - 5_090_000.0)


def write_tile(
    directory: Path,
    west: float,
    south: float,
    crs: str = CRS,
    nodata: bool = False,
    name: str | None = None,
) -> Path:
    """One 1 km tile with 10 m pixels, named like Bavarian DGM1 tiles."""
    import rasterio
    from rasterio.transform import from_origin

    n = round(TILE_M / PIXEL_M)
    centres = (np.arange(n) + 0.5) * PIXEL_M
    x, y = np.meshgrid(west + centres, south + TILE_M - centres)
    data = terrain(x, y).astype(np.float32)
    if nodata:
        data[:, :] = -9999.0
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (name or f"32{int(west // 1000)}_{int(south // 1000)}.tif")
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=n,
        width=n,
        count=1,
        dtype="float32",
        crs=crs,
        transform=from_origin(west, south + TILE_M, PIXEL_M, PIXEL_M),
        nodata=-9999.0,
    ) as dst:
        dst.write(data, 1)
    return path


def tiles_covering(directory: Path, lat: FloatArray, lon: FloatArray) -> int:
    x, y = TO_UTM.transform(lon, lat)
    wests = np.unique(np.floor(np.asarray(x) / TILE_M) * TILE_M)
    souths = np.unique(np.floor(np.asarray(y) / TILE_M) * TILE_M)
    for w in wests:
        for s in souths:
            write_tile(directory, float(w), float(s))
    return len(wests) * len(souths)


def to_wgs84(x: float, y: float) -> tuple[float, float]:
    lon, lat = Transformer.from_crs(CRS, "EPSG:4326", always_xy=True).transform(x, y)
    return lat, lon


@pytest.fixture
def ws(tmp_path: Path) -> Iterator[Workspace]:
    workspace = Workspace(tmp_path)
    yield workspace
    workspace.close()


def provider(tmp_path: Path) -> LocalTileProvider:
    return LocalTileProvider(tmp_path / "dem" / "BY_DGM1", "BY_DGM1")


# --- sampling -----------------------------------------------------------------------


def test_samples_match_the_terrain_across_tile_seams(tmp_path: Path) -> None:
    directory = tmp_path / "dem" / "BY_DGM1"
    write_tile(directory, 420_000.0, 5_095_000.0)
    write_tile(directory, 421_000.0, 5_095_000.0)  # east neighbour
    write_tile(directory, 420_000.0, 5_096_000.0)  # north neighbour
    # Inside, and within half a pixel of the seams (needs the neighbour ring).
    xs = np.array([420_500.0, 420_999.0, 421_001.0, 420_300.0, 420_300.0])
    ys = np.array([5_095_500.0, 5_095_400.0, 5_095_400.0, 5_095_999.0, 5_096_001.0])
    points = [to_wgs84(x, y) for x, y in zip(xs, ys, strict=True)]
    lat = np.array([p[0] for p in points])
    lon = np.array([p[1] for p in points])

    sample = provider(tmp_path).sample(lat, lon)
    np.testing.assert_allclose(sample.elevation_m, terrain(xs, ys), atol=1e-3)
    assert sample.dataset.surface == "terrain"
    assert {t.name for t in sample.tiles} == {"32420_5095", "32421_5095", "32420_5096"}
    assert not any(t.downloaded for t in sample.tiles)


def test_points_outside_the_tiles_are_missing_not_invented(tmp_path: Path) -> None:
    write_tile(tmp_path / "dem" / "BY_DGM1", 420_000.0, 5_095_000.0)
    inside = to_wgs84(420_500.0, 5_095_500.0)
    outside = to_wgs84(425_500.0, 5_095_500.0)
    sample = provider(tmp_path).sample(
        np.array([inside[0], outside[0]]), np.array([inside[1], outside[1]])
    )
    assert not np.isnan(sample.elevation_m[0]) and np.isnan(sample.elevation_m[1])


def test_no_data_pixels_stay_missing(tmp_path: Path) -> None:
    write_tile(tmp_path / "dem" / "BY_DGM1", 420_000.0, 5_095_000.0, nodata=True)
    lat, lon = to_wgs84(420_500.0, 5_095_500.0)
    sample = provider(tmp_path).sample(np.array([lat]), np.array([lon]))
    assert np.isnan(sample.elevation_m[0])


def test_missing_or_unusable_tiles(tmp_path: Path) -> None:
    lat, lon = to_wgs84(420_500.0, 5_095_500.0)
    points = (np.array([lat]), np.array([lon]))
    with pytest.raises(DemUnavailableError, match="no GeoTIFF tiles"):
        provider(tmp_path).sample(*points)

    directory = tmp_path / "dem" / "BY_DGM1"
    write_tile(directory, 500_000.0, 5_095_000.0)
    with pytest.raises(DemUnavailableError, match="covers this track"):
        provider(tmp_path).sample(*points)

    write_tile(directory / "other", 420_000.0, 5_095_000.0, crs="EPSG:25833")
    with pytest.raises(DemUnavailableError, match="mix CRS"):
        provider(tmp_path).sample(*points)


def test_footprints_are_indexed_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "dem" / "BY_DGM1"
    write_tile(directory, 420_000.0, 5_095_000.0)
    opened: list[str] = []
    original = local._footprint

    def counting(path: Path, rel: str, size: int, mtime_ns: int) -> local.LocalTile:
        opened.append(rel)
        return original(path, rel, size, mtime_ns)

    monkeypatch.setattr(local, "_footprint", counting)
    assert len(provider(tmp_path).index()) == 1
    assert len(provider(tmp_path).index()) == 1
    assert opened == ["32420_5095.tif"]  # second call used _index.json
    index = json.loads((directory / "_index.json").read_text(encoding="utf-8"))
    assert index["tiles"][0]["crs"] == CRS

    write_tile(directory, 421_000.0, 5_095_000.0)
    assert len(provider(tmp_path).index()) == 2
    assert opened == ["32420_5095.tif", "32421_5095.tif"]
    (directory / "_index.json").write_text("not json", encoding="utf-8")
    assert len(provider(tmp_path).index()) == 2  # rebuilt, not an error


def test_open_dem_picks_the_provider(tmp_path: Path) -> None:
    assert isinstance(open_dem(tmp_path, "BY_DGM1", None), LocalTileProvider)
    assert isinstance(open_dem(tmp_path, "COP30", None), DemProvider)
    with pytest.raises(ValueError, match="unknown DEM dataset"):
        open_dem(tmp_path, "nope", None)


# --- use cases, CLI, API ---------------------------------------------------------


def enrich_route(ws: Workspace, tmp_path: Path) -> int:
    track_id = import_file(ws, "route.gpx", ROUTE).tracks[0].id
    with ws.db.transaction() as repo:
        track = repo.load_track(track_id)
    tiles_covering(tmp_path / "dem" / "BY_DGM1", track.lat_deg, track.lon_deg)
    return track_id


def test_enrich_and_analyse_with_local_terrain_model(
    ws: Workspace, tmp_path: Path
) -> None:
    track_id = enrich_route(ws, tmp_path)
    view = enrich_track(ws, track_id, provider(tmp_path))
    assert view.comparison.reference_coverage == pytest.approx(1.0)
    assert view.profile.surface == "terrain"
    assert "Bayerische Vermessungsverwaltung" in view.profile.attribution

    analysed = show_track(ws, track_id, AnalysisParams(elevation="dem:BY_DGM1"))
    assert analysed.dem is not None and analysed.dem.dataset_id == "BY_DGM1"
    x, y = TO_UTM.transform(analysed.track.lon_deg, analysed.track.lat_deg)
    expected = terrain(np.asarray(x), np.asarray(y))
    # Knots every 20 m, linear in between: on a plane only the projection's
    # curvature between knots remains.
    np.testing.assert_allclose(analysed.track.ele_raw_m, expected, atol=0.05)

    batch = enrich_all(ws, provider(tmp_path))
    assert batch.skipped_track_ids == (track_id,) and not batch.enriched


def run(*argv: str) -> tuple[int, str]:
    out = io.StringIO()
    return main(list(argv), out=out), out.getvalue()


def test_cli_and_api_with_local_tiles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    from alpha.api import create_app

    monkeypatch.delenv("OPENTOPOGRAPHY_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)  # no .env with a key
    data = tmp_path / "data"
    run("--data-dir", str(data), "import", str(FIXTURES / "route_only.gpx"))
    code, text = run("--data-dir", str(data), "enrich", "1", "--dataset", "BY_DGM1")
    assert code == EXIT_UNAVAILABLE and "no GeoTIFF tiles" in text

    ws = Workspace(data)
    with ws.db.transaction() as repo:
        track = repo.load_track(1)
    ws.close()
    tiles_covering(data / "dem" / "BY_DGM1", track.lat_deg, track.lon_deg)
    code, text = run("--data-dir", str(data), "enrich", "1", "--dataset", "BY_DGM1")
    assert code == EXIT_OK, text
    assert "local tiles (nothing downloaded)" in text
    assert "nothing is sent anywhere" in text and "OpenTopography" not in text
    assert "Attribution: Datenquelle: Bayerische Vermessungsverwaltung" in text

    code, text = run("--data-dir", str(data), "list")
    assert "BY_DGM1" in text

    with TestClient(create_app(data)) as client:  # no API key at all
        info = client.get("/dem/datasets").json()
        local_ds = next(d for d in info["datasets"] if d["id"] == "BY_DGM1")
        assert local_ds["source"] == "local" and local_ds["obtain"]
        check = client.get("/tracks/1/dem/BY_DGM1").json()
        assert check["elevation_series"]["surface"] == "terrain"
        refreshed = client.post(
            "/tracks/1/dem", params={"dataset": "BY_DGM1", "refresh": True}
        )
        assert refreshed.status_code == 200, refreshed.text
        assert refreshed.json()["downloaded_tiles"] == []
        shown = client.get("/tracks/1", params={"elevation": "dem:BY_DGM1"}).json()
        assert "Bayerische" in shown["elevation_series"]["attribution"]
