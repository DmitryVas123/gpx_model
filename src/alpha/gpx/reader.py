"""GPX bytes -> domain tracks (ARCHITECTURE.md §4.6).

The input bytes are never modified. Decoding follows the XML declaration
because gpxpy decodes raw bytes as UTF-8 regardless of the declared encoding.
"""

import codecs
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import gpxpy
import gpxpy.gpx
import numpy as np

from alpha.domain.classify import infer_elevation_source, infer_kind
from alpha.domain.quality import QualityWarning, WarningCode
from alpha.domain.track import Track

DEFAULT_MAX_BYTES = 50 * 1024 * 1024

_XML_ENCODING = re.compile(
    rb"""^<\?xml[^>]*?encoding\s*=\s*["']([A-Za-z0-9._-]+)["']"""
)


class ReadErrorCode(StrEnum):
    EMPTY_FILE = "EMPTY_FILE"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    UNSUPPORTED_ENCODING = "UNSUPPORTED_ENCODING"
    DECODE_ERROR = "DECODE_ERROR"
    INVALID_XML = "INVALID_XML"
    INVALID_GPX = "INVALID_GPX"


class GpxReadError(Exception):
    """The file cannot be read at all. Partial problems are warnings instead."""

    def __init__(self, code: ReadErrorCode, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class SourceElement(StrEnum):
    TRK = "trk"
    RTE = "rte"


@dataclass(frozen=True)
class SourceTrack:
    """A track plus where it came from in the file and what the reader excluded."""

    element: SourceElement
    index_in_element: int  # n-th <trk> or n-th <rte>
    track: Track
    read_warnings: tuple[QualityWarning, ...]


@dataclass(frozen=True)
class GpxDocument:
    creator: str | None
    gpx_version: str | None
    waypoint_count: int
    tracks: tuple[SourceTrack, ...]
    warnings: tuple[QualityWarning, ...]  # file-level


def read_gpx(data: bytes, max_bytes: int = DEFAULT_MAX_BYTES) -> GpxDocument:
    """Parse GPX bytes. Raises :class:`GpxReadError` if nothing can be read."""
    if len(data) == 0:
        raise GpxReadError(ReadErrorCode.EMPTY_FILE, "The file is empty.")
    if len(data) > max_bytes:
        raise GpxReadError(
            ReadErrorCode.FILE_TOO_LARGE,
            f"The file is {len(data)} bytes; the limit is {max_bytes}.",
        )

    gpx = _parse(_decode(data))
    creator = _optional_str(gpx.creator)

    tracks: list[SourceTrack] = []
    for i, trk in enumerate(gpx.tracks):
        segments = [list(seg.points) for seg in trk.segments]
        tracks.append(
            _build(SourceElement.TRK, i, trk.name, trk.type, segments, creator)
        )
    for i, rte in enumerate(gpx.routes):
        tracks.append(
            _build(
                SourceElement.RTE,
                i,
                rte.name,
                getattr(rte, "type", None),
                [list(rte.points)],
                creator,
            )
        )

    file_warnings: list[QualityWarning] = []
    if not any(st.track.point_count > 0 for st in tracks):
        file_warnings.append(
            QualityWarning(
                WarningCode.NO_TRACKS,
                "The file contains no <trk> or <rte> with valid points.",
            )
        )

    return GpxDocument(
        creator=creator,
        gpx_version=_optional_str(gpx.version),
        waypoint_count=len(gpx.waypoints),
        tracks=tuple(tracks),
        warnings=tuple(file_warnings),
    )


_BOMS = (
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)


def _detect_encoding(data: bytes) -> str:
    """Byte-order mark first, then the XML declaration, else UTF-8 (XML default)."""
    for bom, encoding in _BOMS:
        if data.startswith(bom):
            return encoding
    match = _XML_ENCODING.match(data[:200])
    return match.group(1).decode("ascii") if match else "utf-8"


def _decode(data: bytes) -> str:
    encoding = _detect_encoding(data)

    try:
        codecs.lookup(encoding)
    except LookupError as exc:
        raise GpxReadError(
            ReadErrorCode.UNSUPPORTED_ENCODING,
            f"The declared encoding '{encoding}' is not supported.",
        ) from exc
    try:
        return data.decode(encoding).lstrip("﻿")
    except UnicodeDecodeError as exc:
        raise GpxReadError(
            ReadErrorCode.DECODE_ERROR,
            f"The file is not valid {encoding} (byte {exc.start}).",
        ) from exc


def _parse(text: str) -> Any:
    try:
        return gpxpy.parse(text)
    except gpxpy.gpx.GPXXMLSyntaxException as exc:
        raise GpxReadError(ReadErrorCode.INVALID_XML, str(exc)) from exc
    except Exception as exc:  # gpxpy raises various types on malformed GPX
        raise GpxReadError(ReadErrorCode.INVALID_GPX, str(exc)) from exc


def _build(
    element: SourceElement,
    index: int,
    name: Any,
    activity_type: Any,
    segments: list[list[Any]],
    creator: str | None,
) -> SourceTrack:
    lat: list[float] = []
    lon: list[float] = []
    ele: list[float] = []
    time: list[float] = []
    seg: list[int] = []
    invalid: list[int] = []

    source_index = 0
    for segment_index, points in enumerate(segments):
        for point in points:
            la, lo = point.latitude, point.longitude
            if _valid_coordinate(la, lo):
                lat.append(float(la))
                lon.append(float(lo))
                ele.append(
                    math.nan if point.elevation is None else float(point.elevation)
                )
                time.append(_to_utc_seconds(point.time))
                seg.append(segment_index)
            else:
                invalid.append(source_index)
            source_index += 1

    time_arr = np.array(time, dtype=np.float64)
    ele_arr = np.array(ele, dtype=np.float64)
    kind, kind_by = infer_kind(time_arr)
    ele_source, ele_by = infer_elevation_source(ele_arr, time_arr, creator)

    track = Track(
        name=_optional_str(name),
        kind=kind,
        kind_inferred_by=kind_by,
        elevation_source=ele_source,
        elevation_source_inferred_by=ele_by,
        activity_type=_optional_str(activity_type),
        lat_deg=np.array(lat, dtype=np.float64),
        lon_deg=np.array(lon, dtype=np.float64),
        ele_raw_m=ele_arr,
        time_utc_s=time_arr,
        source_segment=np.array(seg, dtype=np.int32),
    )

    read_warnings: tuple[QualityWarning, ...] = ()
    if invalid:
        read_warnings = (
            QualityWarning(
                WarningCode.INVALID_COORDINATE,
                f"{len(invalid)} point(s) with out-of-range or missing coordinates "
                "were excluded. Indices refer to the order in the file, not to the "
                "track arrays.",
                tuple(invalid),
                len(invalid),
            ),
        )

    return SourceTrack(element, index, track, read_warnings)


def _valid_coordinate(lat: Any, lon: Any) -> bool:
    if lat is None or lon is None:
        return False
    la, lo = float(lat), float(lon)
    return math.isfinite(la) and math.isfinite(lo) and abs(la) <= 90 and abs(lo) <= 180


def _to_utc_seconds(value: Any) -> float:
    if value is None:
        return math.nan
    if not isinstance(value, datetime):
        return math.nan
    if value.tzinfo is None:  # GPX times are UTC by specification
        value = value.replace(tzinfo=UTC)
    return float(value.timestamp())


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
