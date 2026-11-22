"""Builders for synthetic tracks and GPX documents (no real locations)."""

from collections.abc import Sequence

import numpy as np
import numpy.typing as npt

from alpha.domain.track import ElevationSource, Inference, Track, TrackKind

# One synthetic origin used by all fixtures. 0.0001 deg latitude ~ 11.1 m.
LAT0 = 46.0
LON0 = 8.0

Floats = Sequence[float] | npt.NDArray[np.float64]


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
