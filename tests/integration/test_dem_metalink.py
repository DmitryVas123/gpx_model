"""Metalink tile downloads (ADR-013) with a fake downloader: no network."""

import io
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest

from alpha.application.elevation import get_tiles, plan_tiles
from alpha.application.tracks import Workspace, import_file
from alpha.cli import EXIT_INVALID, EXIT_OK, EXIT_UNAVAILABLE, main
from alpha.dem import DATASETS, DemDownloadError, MetalinkError, metalink
from alpha.dem.metalink import (
    RemoteTile,
    km_tile_bounds,
    read_metalink,
    tiles_for_points,
)
from tests.integration.test_dem_local import TO_UTM, write_tile

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
ROUTE = (FIXTURES / "route_only.gpx").read_bytes()  # around 46.0 N, 8.0 E
DGM1 = DATASETS["BY_DGM1"]
HOST1 = "https://download1.bayernwolke.de/a/dgm/dgm1/"
HOST2 = "https://download2.bayernwolke.de/a/dgm/dgm1/"


def meta4(names: list[str], hosts: tuple[str, ...] = (HOST1, HOST2)) -> bytes:
    files = "".join(
        f'<file name="{n}">' + "".join(f"<url>{h}{n}</url>" for h in hosts) + "</file>"
        for n in names
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<metalink xmlns="urn:ietf:params:xml:ns:metalink">{files}</metalink>'
    ).encode()


class FakeDownload:
    """Writes the GeoTIFF a tile name stands for; can fail on some hosts."""

    def __init__(self, failing_host: str | None = None, shift_m: float = 0.0) -> None:
        self.urls: list[str] = []
        self.failing_host = failing_host
        self.shift_m = shift_m

    def __call__(self, url: str, destination: Path) -> None:
        self.urls.append(url)
        if self.failing_host and self.failing_host in url:
            raise DemDownloadError(f"{url} returned HTTP 503")
        bounds = km_tile_bounds(destination.name)
        assert bounds is not None
        write_tile(
            destination.parent,
            bounds[0],
            bounds[1] + self.shift_m,
            name=destination.name,
        )


# --- reading the metalink --------------------------------------------------------


def test_reads_tiles_and_mirrors() -> None:
    tiles = read_metalink(meta4(["608_5268.tif", "594_5255.tif"]), DGM1)
    assert [t.name for t in tiles] == ["608_5268.tif", "594_5255.tif"]
    assert tiles[0].urls == (HOST1 + "608_5268.tif", HOST2 + "608_5268.tif")
    assert km_tile_bounds("608_5268.tif") == (
        608_000.0,
        5_268_000.0,
        609_000.0,
        5_269_000.0,
    )


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (meta4(["../evil.tif"]), "unsafe tile name"),
        (meta4(["a/b.tif"]), "unsafe tile name"),
        (meta4(["x.exe"]), "unsafe tile name"),
        (meta4(["1_2.tif"], hosts=("https://example.org/",)), "known host"),
        (meta4(["1_2.tif"], hosts=("http://download1.bayernwolke.de/",)), "known host"),
        (meta4([]), "lists no files"),
        (b"<metalink/>", "not a metalink 4"),
        (b"not xml", "not a readable metalink"),
    ],
)
def test_refuses_unsafe_or_foreign_metalinks(data: bytes, message: str) -> None:
    with pytest.raises(MetalinkError, match=message):
        read_metalink(data, DGM1)


def test_only_known_hosts_are_kept() -> None:
    data = meta4(["1_2.tif"], hosts=("https://example.org/", HOST2))
    assert read_metalink(data, DGM1)[0].urls == (HOST2 + "1_2.tif",)


def test_track_selection_adds_seam_neighbours() -> None:
    tiles = [RemoteTile(f"{e}_{n}.tif", ("u",)) for e in (600, 601) for n in (5250,)]
    inside = tiles_for_points(tiles, np.array([600_500.0]), np.array([5_250_500.0]))
    assert [t.name for t in inside[0]] == ["600_5250.tif"] and inside[1] == 0
    near_edge = tiles_for_points(tiles, np.array([600_999.0]), np.array([5_250_500.0]))
    assert [t.name for t in near_edge[0]] == ["600_5250.tif", "601_5250.tif"]
    outside = tiles_for_points(tiles, np.array([700_500.0]), np.array([5_250_500.0]))
    assert outside == ([], 1)
    with pytest.raises(MetalinkError, match="pattern"):
        tiles_for_points(
            [RemoteTile("tile.tif", ("u",))], np.array([0.0]), np.array([0.0])
        )


# --- planning and downloading ------------------------------------------------------


@pytest.fixture
def ws(tmp_path: Path) -> Iterator[Workspace]:
    workspace = Workspace(tmp_path)
    yield workspace
    workspace.close()


def route_tile_names(ws: Workspace) -> list[str]:
    track_id = import_file(ws, "route.gpx", ROUTE).tracks[0].id
    with ws.db.transaction() as repo:
        track = repo.load_track(track_id)
    x, y = TO_UTM.transform(track.lon_deg, track.lat_deg)
    keys = {
        (int(e), int(n))
        for e, n in zip(
            np.floor(np.asarray(x) / 1000), np.floor(np.asarray(y) / 1000), strict=True
        )
    }
    return [f"{e}_{n}.tif" for e, n in sorted(keys)]


def test_plan_and_download_for_tracks(ws: Workspace, tmp_path: Path) -> None:
    names = route_tile_names(ws)
    data = meta4([*names, "100_5000.tif"])  # one tile far away
    dem_dir = tmp_path / "dem"

    everything = plan_tiles(ws, dem_dir, data, "BY_DGM1", for_tracks=False)
    assert everything.listed == len(names) + 1 == everything.to_download
    plan = plan_tiles(ws, dem_dir, data, "BY_DGM1", for_tracks=True)
    assert {t.name for t in plan.selected} >= set(names)
    assert "100_5000.tif" not in {t.name for t in plan.selected}

    fake = FakeDownload(failing_host="download1")
    result = get_tiles(plan, fetch=fake)
    assert len(result.downloaded) == len(plan.selected)
    assert all("download2" in u for u in fake.urls[1::2])  # mirror used
    again = plan_tiles(ws, dem_dir, data, "BY_DGM1", for_tracks=True)
    assert again.to_download == 0 and again.already_present == len(plan.selected)


def test_wrong_tile_is_deleted_and_stops(ws: Workspace, tmp_path: Path) -> None:
    names = route_tile_names(ws)
    plan = plan_tiles(ws, tmp_path / "dem", meta4(names), "BY_DGM1", for_tracks=False)
    with pytest.raises(MetalinkError, match="naming assumption"):
        get_tiles(plan, fetch=FakeDownload(shift_m=1000.0))
    assert not list((tmp_path / "dem" / "BY_DGM1").glob("*.tif"))


def test_unreachable_mirrors_stop_the_download(ws: Workspace, tmp_path: Path) -> None:
    names = route_tile_names(ws)
    plan = plan_tiles(ws, tmp_path / "dem", meta4(names), "BY_DGM1", for_tracks=False)
    fake = FakeDownload(failing_host="bayernwolke")
    with pytest.raises(DemDownloadError, match="no mirror"):
        get_tiles(plan, fetch=fake)
    assert len(fake.urls) == 2  # one tile, both mirrors; then stopped


def test_only_metalink_datasets(ws: Workspace, tmp_path: Path) -> None:
    with pytest.raises(MetalinkError, match="cannot be downloaded"):
        plan_tiles(ws, tmp_path, meta4(["1_2.tif"]), "COP30", for_tracks=False)


# --- CLI ------------------------------------------------------------------------------


def run(*argv: str) -> tuple[int, str]:
    out = io.StringIO()
    return main(list(argv), out=out), out.getvalue()


def test_cli_get_tiles_asks_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = tmp_path / "data"
    run("--data-dir", str(data), "import", str(FIXTURES / "route_only.gpx"))
    ws = Workspace(data)
    names = route_tile_names(ws)  # imports the route again: idempotent
    ws.close()
    link = tmp_path / "dgm1.meta4"
    link.write_bytes(meta4(names))
    fake = FakeDownload()
    monkeypatch.setattr(metalink, "download", fake)

    code, text = run("--data-dir", str(data), "get-tiles", str(link), "--for-tracks")
    assert code == EXIT_OK and "Repeat with --yes" in text
    assert "1 km squares your tracks cross" in text and not fake.urls

    code, text = run(
        "--data-dir", str(data), "get-tiles", str(link), "--for-tracks", "--yes"
    )
    assert code == EXIT_OK, text
    assert f"Downloaded {len(names)} tile(s)" in text
    code, text = run("--data-dir", str(data), "enrich", "1", "--dataset", "BY_DGM1")
    assert code == EXIT_OK and "local tiles" in text

    bad = tmp_path / "bad.meta4"
    bad.write_bytes(meta4(["../x.tif"]))
    assert run("--data-dir", str(data), "get-tiles", str(bad))[0] == EXIT_INVALID
    monkeypatch.setattr(metalink, "download", FakeDownload(failing_host="bayernwolke"))
    link.write_bytes(meta4(["700_5000.tif"]))
    code, text = run("--data-dir", str(data), "get-tiles", str(link), "--yes")
    assert code == EXIT_UNAVAILABLE and "no mirror" in text
