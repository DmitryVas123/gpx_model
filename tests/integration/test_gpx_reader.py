import codecs
import math
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from alpha.domain.quality import WarningCode
from alpha.domain.track import ElevationSource, Inference, TrackKind
from alpha.gpx.reader import GpxReadError, ReadErrorCode, SourceElement, read_gpx
from tests.helpers import LAT0, gpx_doc, trk, trkpt

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def epoch(iso: str) -> float:
    return datetime.fromisoformat(iso).astimezone(UTC).timestamp()


# --- whole-file errors -------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"", ReadErrorCode.EMPTY_FILE),
        (b"not xml at all", ReadErrorCode.INVALID_XML),
        (b"<gpx><trk>", ReadErrorCode.INVALID_XML),
        (
            gpx_doc('<trk><trkseg><trkpt lat="abc" lon="8"/></trkseg></trk>'),
            ReadErrorCode.INVALID_GPX,
        ),
        (
            b'<?xml version="1.0" encoding="x-no-such-codec"?><gpx/>',
            ReadErrorCode.UNSUPPORTED_ENCODING,
        ),
        (
            b'<?xml version="1.0" encoding="UTF-8"?><gpx>\xff\xfe</gpx>',
            ReadErrorCode.DECODE_ERROR,
        ),
    ],
    ids=["empty", "not-xml", "truncated", "bad-number", "unknown-codec", "bad-utf8"],
)
def test_unreadable_files_raise_structured_errors(
    data: bytes, code: ReadErrorCode
) -> None:
    with pytest.raises(GpxReadError) as excinfo:
        read_gpx(data)
    assert excinfo.value.code is code
    assert excinfo.value.message


def test_size_limit() -> None:
    data = gpx_doc(trk([trkpt(LAT0)]))
    with pytest.raises(GpxReadError) as excinfo:
        read_gpx(data, max_bytes=len(data) - 1)
    assert excinfo.value.code is ReadErrorCode.FILE_TOO_LARGE
    assert read_gpx(data, max_bytes=len(data)).tracks


# --- file-level warnings -------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    ["", '<wpt lat="46" lon="8"/>', "<trk><trkseg></trkseg></trk>"],
    ids=["empty-gpx", "waypoint-only", "empty-trk"],
)
def test_no_tracks_is_a_warning_not_an_error(body: str) -> None:
    doc = read_gpx(gpx_doc(body))
    assert [w.code for w in doc.warnings] == [WarningCode.NO_TRACKS]


def test_non_gpx_xml_reports_no_tracks() -> None:
    doc = read_gpx(b"<foo/>")
    assert [w.code for w in doc.warnings] == [WarningCode.NO_TRACKS]


# --- tracks, segments, routes ------------------------------------------------


def test_activity_fixture_with_two_segments() -> None:
    doc = read_gpx((FIXTURES / "activity_two_segments.gpx").read_bytes())
    assert doc.creator == "alpha-tests"
    assert doc.gpx_version == "1.1"
    assert doc.warnings == ()
    (source,) = doc.tracks
    t = source.track
    assert source.element is SourceElement.TRK
    assert t.name == "Synthetic activity"
    assert t.activity_type == "hiking"
    assert t.point_count == 8
    assert t.source_segment.tolist() == [0, 0, 0, 0, 1, 1, 1, 1]
    assert t.ele_raw_m[0] == 1000.0
    assert t.time_utc_s[0] == epoch("2026-01-01T08:00:00+00:00")
    assert (t.kind, t.kind_inferred_by) == (TrackKind.ACTIVITY, Inference.HEURISTIC)
    assert t.elevation_source is ElevationSource.UNKNOWN


def test_route_only_fixture() -> None:
    doc = read_gpx((FIXTURES / "route_only.gpx").read_bytes())
    assert doc.waypoint_count == 1
    (source,) = doc.tracks
    t = source.track
    assert source.element is SourceElement.RTE
    assert t.name == "Маршрут к хижине"
    assert t.point_count == 3
    assert t.source_segment.tolist() == [0, 0, 0]
    assert t.kind is TrackKind.PLANNED
    assert t.elevation_source is ElevationSource.PLANNER_DEM
    assert np.isnan(t.time_utc_s).all()


def test_tracks_and_routes_are_both_read_in_file_order() -> None:
    body = (
        trk([trkpt(LAT0)], name="a")
        + trk([trkpt(LAT0)], name="b")
        + f"<rte><name>c</name>{trkpt(LAT0, tag='rtept')}</rte>"
    )
    doc = read_gpx(gpx_doc(body))
    assert [(s.element, s.index_in_element, s.track.name) for s in doc.tracks] == [
        (SourceElement.TRK, 0, "a"),
        (SourceElement.TRK, 1, "b"),
        (SourceElement.RTE, 0, "c"),
    ]


def test_missing_elevation_and_time_become_nan() -> None:
    doc = read_gpx(gpx_doc(trk([trkpt(LAT0, ele=5.0), trkpt(LAT0 + 0.001)])))
    t = doc.tracks[0].track
    assert t.ele_raw_m[0] == 5.0
    assert math.isnan(t.ele_raw_m[1])
    assert np.isnan(t.time_utc_s).all()


# --- coordinates -------------------------------------------------------------


def test_out_of_range_points_are_excluded_and_reported() -> None:
    points = [trkpt(LAT0), trkpt(95.0), trkpt(LAT0 + 0.001), trkpt(LAT0, lon=200.0)]
    source = read_gpx(gpx_doc(trk(points))).tracks[0]
    assert source.track.point_count == 2
    (w,) = source.read_warnings
    assert w.code is WarningCode.INVALID_COORDINATE
    assert w.point_indices == (1, 3)  # positions in the file


# --- timestamps --------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-01-01T10:00:00Z", "2026-01-01T10:00:00+00:00"),
        ("2026-01-01T10:00:00", "2026-01-01T10:00:00+00:00"),  # naive = UTC
        ("2026-01-01T10:00:00+02:00", "2026-01-01T08:00:00+00:00"),
        ("2026-01-01T10:00:00.500Z", "2026-01-01T10:00:00.500+00:00"),
    ],
)
def test_timestamps_are_converted_to_utc_seconds(raw: str, expected: str) -> None:
    doc = read_gpx(gpx_doc(trk([trkpt(LAT0, time=raw)])))
    assert doc.tracks[0].track.time_utc_s[0] == pytest.approx(epoch(expected))


# --- encodings ---------------------------------------------------------------

CYRILLIC = "Маршрут на вершину"


@pytest.mark.parametrize("encoding", ["UTF-8", "windows-1251"])
def test_declared_encoding_is_respected(encoding: str) -> None:
    data = gpx_doc(trk([trkpt(LAT0)], name=CYRILLIC), encoding=encoding)
    assert read_gpx(data).tracks[0].track.name == CYRILLIC


def test_utf8_bom_and_missing_declaration() -> None:
    data = codecs.BOM_UTF8 + gpx_doc(
        trk([trkpt(LAT0)], name=CYRILLIC), declaration=False
    )
    assert read_gpx(data).tracks[0].track.name == CYRILLIC


def test_utf16_with_bom() -> None:
    text = gpx_doc(trk([trkpt(LAT0)], name=CYRILLIC), declaration=False).decode()
    data = codecs.BOM_UTF16_LE + text.encode("utf-16-le")
    assert read_gpx(data).tracks[0].track.name == CYRILLIC
