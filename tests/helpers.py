"""Builders for synthetic tracks and GPX documents (no real locations)."""

from collections.abc import Callable, Sequence
from typing import Any

import numpy as np
import numpy.typing as npt

from alpha.domain.track import ElevationSource, Inference, Track, TrackKind

# One synthetic origin used by all fixtures. 0.0001 deg latitude ~ 11.1 m.
LAT0 = 46.0
LON0 = 8.0

Floats = Sequence[float] | npt.NDArray[Any]


def make_track(
    lat: Floats,
    lon: Floats | None = None,
    ele: Floats | None = None,
    time: Floats | None = None,
    segment: Sequence[int] | None = None,
    kind: TrackKind = TrackKind.UNKNOWN,
    kind_inferred_by: Inference = Inference.USER,
    elevation_source: ElevationSource = ElevationSource.UNKNOWN,
    elevation_source_inferred_by: Inference = Inference.USER,
) -> Track:
    n = len(lat)
    return Track(
        name="synthetic",
        kind=kind,
        kind_inferred_by=kind_inferred_by,
        elevation_source=elevation_source,
        elevation_source_inferred_by=elevation_source_inferred_by,
        activity_type=None,
        lat_deg=np.array(lat, dtype=np.float64),
        lon_deg=np.array(lon if lon is not None else [LON0] * n, dtype=np.float64),
        ele_raw_m=np.array(ele if ele is not None else [np.nan] * n, dtype=np.float64),
        time_utc_s=np.array(
            time if time is not None else [np.nan] * n, dtype=np.float64
        ),
        source_segment=np.array(
            segment if segment is not None else [0] * n, dtype=np.int32
        ),
    )


def trkpt(
    lat: float,
    lon: float = LON0,
    ele: float | None = None,
    time: str | None = None,
    tag: str = "trkpt",
) -> str:
    inner = ""
    if ele is not None:
        inner += f"<ele>{ele}</ele>"
    if time is not None:
        inner += f"<time>{time}</time>"
    return f'<{tag} lat="{lat}" lon="{lon}">{inner}</{tag}>'


def gpx_doc(
    body: str,
    encoding: str = "UTF-8",
    creator: str = "alpha-tests",
    declaration: bool = True,
) -> bytes:
    decl = f'<?xml version="1.0" encoding="{encoding}"?>\n' if declaration else ""
    text = (
        f"{decl}"
        f'<gpx version="1.1" creator="{creator}" '
        f'xmlns="http://www.topografix.com/GPX/1/1">{body}</gpx>'
    )
    return text.encode(encoding)


def trk(
    *segments: Sequence[str], name: str | None = None, type_: str | None = None
) -> str:
    head = ""
    if name is not None:
        head += f"<name>{name}</name>"
    if type_ is not None:
        head += f"<type>{type_}</type>"
    segs = "".join(f"<trkseg>{''.join(points)}</trkseg>" for points in segments)
    return f"<trk>{head}{segs}</trk>"


# Degrees of latitude per metre near LAT0 (approximate; tests that need exact
# distances measure them with cumulative_distance_m instead).
DEG_PER_METRE = 1 / 111_150.0


def line_track(
    spacing_m: float,
    length_m: float,
    ele_of_distance: Callable[[npt.NDArray[np.float64]], npt.NDArray[np.float64]]
    | None = None,
    seconds_per_point: float | None = None,
) -> Track:
    """Straight track due north with points every ~``spacing_m`` metres and
    elevation given as a function of horizontal distance from the start."""
    from alpha.domain.profile import cumulative_distance_m

    n = round(length_m / spacing_m) + 1
    lat = LAT0 + np.arange(n) * spacing_m * DEG_PER_METRE
    distance = cumulative_distance_m(make_track(lat))
    ele = ele_of_distance(distance) if ele_of_distance else None
    time = np.arange(n) * seconds_per_point if seconds_per_point else None
    return make_track(lat, ele=ele, time=time)


# --- synthetic DEM tiles (no network) ---


def plane_elevation(
    lat: npt.NDArray[np.float64], lon: npt.NDArray[np.float64]
) -> npt.NDArray[np.float64]:
    """A tilted plane: bilinear interpolation reproduces it exactly."""
    return 1000.0 + 20_000.0 * (lat - LAT0) + 5_000.0 * (lon - LON0)


def geotiff_for_bounds(
    bounds: tuple[float, float, float, float],
    elevation: Callable[
        [npt.NDArray[np.float64], npt.NDArray[np.float64]], npt.NDArray[np.float64]
    ] = plane_elevation,
    resolution_deg: float = 0.001,
) -> bytes:
    """A north-up float32 GeoTIFF covering ``(south, north, west, east)``."""
    from rasterio.io import MemoryFile
    from rasterio.transform import from_origin

    south, north, west, east = bounds
    cols = round((east - west) / resolution_deg)
    rows = round((north - south) / resolution_deg)
    lon = west + (np.arange(cols) + 0.5) * resolution_deg
    lat = north - (np.arange(rows) + 0.5) * resolution_deg
    grid_lon, grid_lat = np.meshgrid(lon, lat)
    data = elevation(grid_lat, grid_lon).astype(np.float32)
    with MemoryFile() as memory:
        with memory.open(
            driver="GTiff",
            height=rows,
            width=cols,
            count=1,
            dtype="float32",
            crs="EPSG:4326",
            transform=from_origin(west, north, resolution_deg, resolution_deg),
        ) as dataset:
            dataset.write(data, 1)
        return bytes(memory.read())


class FakeFetch:
    """Stands in for the OpenTopography download and counts calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[float, float, float, float]]] = []

    def __call__(
        self, dataset_id: str, bounds: tuple[float, float, float, float], api_key: str
    ) -> bytes:
        assert api_key, "the provider must pass the key"
        self.calls.append((dataset_id, bounds))
        return geotiff_for_bounds(bounds)
