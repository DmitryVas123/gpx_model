"""Download local DEM tiles listed in a metalink file (ADR-013).

Some providers (Bavarian open data) offer a region only as a metalink
(RFC 5854): an XML list of tile files with mirror URLs. ALPHA reads it,
selects tiles, and downloads them into ``<dem dir>/<dataset>/``, only when
the user asks (``alpha get-tiles ... --yes``).

The metalink is untrusted input:

-   file names must be plain tile names (no paths);
-   only HTTPS URLs on the dataset's known hosts are used;
-   every download must be a GeoTIFF whose footprint matches its name.
"""

import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import numpy as np

from alpha.dem.datasets import DemDataset
from alpha.dem.opentopography import DemDownloadError
from alpha.dem.sampling import FloatArray

NS = "{urn:ietf:params:xml:ns:metalink}"
MAX_METALINK_BYTES = 20 * 1024 * 1024
MAX_TILE_BYTES = 256 * 1024 * 1024
TIMEOUT_S = 120
SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}\.tiff?")
KM_NAME = re.compile(r"(\d{3,4})_(\d{4})\.tiff?")
TILE_M = 1000.0

# (url, destination) -> None; writes the file or raises DemDownloadError.
Downloader = Callable[[str, Path], None]


class MetalinkError(Exception):
    """The metalink cannot be used (format, unsafe names, unknown hosts)."""


@dataclass(frozen=True)
class RemoteTile:
    name: str
    urls: tuple[str, ...]


def read_metalink(data: bytes, dataset: DemDataset) -> list[RemoteTile]:
    if len(data) > MAX_METALINK_BYTES:
        raise MetalinkError("metalink file is unexpectedly large")
    try:
        root = ET.fromstring(data)  # no external entities are resolved
    except ET.ParseError as exc:
        raise MetalinkError(f"not a readable metalink file: {exc}") from None
    if root.tag != f"{NS}metalink":
        raise MetalinkError("not a metalink 4 file (urn:ietf:params:xml:ns:metalink)")

    tiles = []
    for item in root.iter(f"{NS}file"):
        name = item.get("name", "")
        if not SAFE_NAME.fullmatch(name):
            raise MetalinkError(f"refusing unsafe tile name {name[:60]!r}")
        urls = [(u.text or "").strip() for u in item.iter(f"{NS}url")]
        usable = tuple(u for u in urls if _allowed(u, dataset.download_hosts))
        if not usable:
            hosts = sorted({urlsplit(u).hostname or "?" for u in urls})
            known = ", ".join(dataset.download_hosts)
            raise MetalinkError(
                f"{name}: no HTTPS URL on a known host for {dataset.id} "
                f"(found {', '.join(hosts)}; known: {known})"
            )
        tiles.append(RemoteTile(name, usable))
    if not tiles:
        raise MetalinkError("the metalink lists no files")
    return tiles


def _allowed(url: str, hosts: Iterable[str]) -> bool:
    parts = urlsplit(url)
    return parts.scheme == "https" and parts.hostname in set(hosts)


# --- tiles named by their 1 km grid corner ---------------------------------------


def km_tile_bounds(name: str) -> tuple[float, float, float, float] | None:
    """``608_5268.tif`` -> the 1 km square with lower-left corner
    (608 000, 5 268 000) in the dataset's projected CRS. ``None`` if the
    name does not follow that pattern. Checked against the real footprint
    after download (``verify_footprint``)."""
    match = KM_NAME.fullmatch(name)
    if match is None:
        return None
    left, bottom = float(match[1]) * TILE_M, float(match[2]) * TILE_M
    return left, bottom, left + TILE_M, bottom + TILE_M


def tiles_for_points(
    tiles: list[RemoteTile], x: FloatArray, y: FloatArray, edge_m: float = 2.0
) -> tuple[list[RemoteTile], int]:
    """Tiles under the points (projected metres), plus the neighbour when a
    point is within ``edge_m`` of a tile edge (needed to interpolate across
    the seam). Returns the tiles and the number of needed 1 km squares that
    the metalink does not list (points outside the downloaded region)."""
    if not all(km_tile_bounds(t.name) for t in tiles):
        raise MetalinkError(
            "tile names do not follow the <km east>_<km north> pattern; "
            "download all tiles instead"
        )
    keys: set[tuple[int, int]] = set()
    for dx in (-edge_m, 0.0, edge_m):
        for dy in (-edge_m, 0.0, edge_m):
            e = np.floor((x + dx) / TILE_M).astype(np.int64)
            n = np.floor((y + dy) / TILE_M).astype(np.int64)
            keys.update(zip(e.tolist(), n.tolist(), strict=True))
    by_key = {}
    for t in tiles:
        bounds = km_tile_bounds(t.name)
        assert bounds is not None
        by_key[(round(bounds[0] / TILE_M), round(bounds[1] / TILE_M))] = t
    selected = [by_key[k] for k in sorted(keys) if k in by_key]
    return selected, sum(1 for k in keys if k not in by_key)


# --- download ------------------------------------------------------------------------


def download(url: str, destination: Path, timeout_s: float = TIMEOUT_S) -> None:
    """Stream one file to ``destination`` (written via a temporary file)."""
    request = urllib.request.Request(
        url, headers={"User-Agent": "alpha-gpx (local analysis tool)"}
    )
    temporary = destination.with_suffix(destination.suffix + ".part")
    try:
        with (
            urllib.request.urlopen(request, timeout=timeout_s) as response,
            temporary.open("wb") as out,
        ):
            total = 0
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_TILE_BYTES:
                    raise DemDownloadError(f"{destination.name}: larger than expected")
                out.write(chunk)
    except urllib.error.HTTPError as exc:
        temporary.unlink(missing_ok=True)
        raise DemDownloadError(f"{url} returned HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError) as exc:
        temporary.unlink(missing_ok=True)
        reason = getattr(exc, "reason", exc)
        raise DemDownloadError(
            f"cannot reach {urlsplit(url).hostname}: {reason}"
        ) from None
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    temporary.replace(destination)


@dataclass(frozen=True)
class TileDownload:
    downloaded: tuple[str, ...]
    already_present: tuple[str, ...]


def download_tiles(
    tiles: list[RemoteTile],
    directory: Path,
    verify: Callable[[Path, RemoteTile], None],
    fetch: Downloader | None = None,
    progress: Callable[[int, int, str], None] | None = None,
) -> TileDownload:
    """Download missing tiles, trying each mirror in turn. Stops at the first
    tile no mirror delivers, so a network problem is not repeated per tile.
    ``verify`` raises if a downloaded file is not the expected tile; the file
    is then deleted."""
    directory.mkdir(parents=True, exist_ok=True)
    get = fetch or download  # resolved at call time so tests can replace it
    done: list[str] = []
    todo = [t for t in tiles if not (directory / t.name).is_file()]
    present = [t.name for t in tiles if (directory / t.name).is_file()]
    for i, tile in enumerate(todo, start=1):
        if progress:
            progress(i, len(todo), tile.name)
        destination = directory / tile.name
        errors = []
        for url in tile.urls:
            try:
                get(url, destination)
                break
            except DemDownloadError as exc:
                errors.append(str(exc))
        else:
            raise DemDownloadError(
                f"{tile.name}: no mirror delivered it ({'; '.join(errors)}); "
                f"{len(done)} tile(s) downloaded before stopping"
            )
        try:
            verify(destination, tile)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        done.append(tile.name)
    return TileDownload(tuple(done), tuple(present))
