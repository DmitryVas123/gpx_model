import math

import numpy as np
import pytest

from alpha.domain.classify import infer_elevation_source, infer_kind
from alpha.domain.track import ElevationSource, Inference, TrackKind

NAN = math.nan


def arr(*values: float) -> np.ndarray[tuple[int], np.dtype[np.float64]]:
    return np.array(values, dtype=np.float64)


@pytest.mark.parametrize(
    ("times", "expected"),
    [
        (arr(), (TrackKind.UNKNOWN, Inference.DEFAULT)),
        (arr(NAN, NAN), (TrackKind.PLANNED, Inference.HEURISTIC)),
        (arr(0.0, 1.0, 2.0), (TrackKind.ACTIVITY, Inference.HEURISTIC)),
        (arr(0.0, NAN, 2.0), (TrackKind.UNKNOWN, Inference.DEFAULT)),
        (arr(5.0, 5.0, 5.0), (TrackKind.UNKNOWN, Inference.DEFAULT)),
    ],
    ids=["empty", "no-times", "all-times", "partial", "constant"],
)
def test_infer_kind(
    times: np.ndarray[tuple[int], np.dtype[np.float64]],
    expected: tuple[TrackKind, Inference],
) -> None:
    assert infer_kind(times) == expected


@pytest.mark.parametrize(
    ("ele", "times", "creator", "expected"),
    [
        (arr(NAN, NAN), arr(NAN, NAN), None, ElevationSource.UNKNOWN),
        (
            arr(1.5, 2.0),
            arr(NAN, NAN),
            "https://mapy.com/",
            ElevationSource.PLANNER_DEM,
        ),
        (arr(1.5, 2.0), arr(0.0, 1.0), "Mapy.cz export", ElevationSource.PLANNER_DEM),
        (arr(852.0, 853.0), arr(NAN, NAN), "other", ElevationSource.PLANNER_DEM),
        (arr(852.0, 853.0), arr(0.0, 1.0), "other", ElevationSource.UNKNOWN),
        (arr(815.7, 815.8), arr(NAN, NAN), "StravaGPX", ElevationSource.UNKNOWN),
        (arr(815.7, 815.8), arr(0.0, 1.0), "StravaGPX", ElevationSource.UNKNOWN),
    ],
    ids=[
        "no-elevation",
        "planner-creator",
        "planner-creator-case-insensitive",
        "integer-no-time",
        "integer-with-time",
        "decimal-no-time",
        "recorded",
    ],
)
def test_infer_elevation_source(
    ele: np.ndarray[tuple[int], np.dtype[np.float64]],
    times: np.ndarray[tuple[int], np.dtype[np.float64]],
    creator: str | None,
    expected: ElevationSource,
) -> None:
    source, inferred_by = infer_elevation_source(ele, times, creator)
    assert source is expected
    expected_by = (
        Inference.DEFAULT
        if expected is ElevationSource.UNKNOWN
        else Inference.HEURISTIC
    )
    assert inferred_by is expected_by
