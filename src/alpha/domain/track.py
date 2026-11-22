"""Track: one planned route or recorded activity, stored column-wise (ADR-008)."""

from dataclasses import dataclass
from enum import StrEnum

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int32]


class TrackKind(StrEnum):
    PLANNED = "planned"
    ACTIVITY = "activity"
    UNKNOWN = "unknown"


class ElevationSource(StrEnum):
    GPS = "gps"
    BAROMETRIC = "barometric"
    PLANNER_DEM = "planner_dem"  # elevation added by a route planner
    DEM_ENRICHED = "dem_enriched"  # elevation sampled by ALPHA (post-MVP)
    UNKNOWN = "unknown"


class Inference(StrEnum):
    USER = "user"  # stated by the user
    HEURISTIC = "heuristic"  # guessed by ALPHA; must be shown as a guess
    DEFAULT = "default"  # nothing known


def _readonly(values: object, dtype: type[np.generic]) -> npt.NDArray[np.generic]:
    array = np.array(values, dtype=dtype, copy=True)
    if array.ndim != 1:
        raise ValueError(f"expected a 1-D array, got shape {array.shape}")
    array.setflags(write=False)
    return array


@dataclass(frozen=True, eq=False)
class Track:
    """Points of one track as equal-length, read-only column arrays.

    Missing elevation and timestamps are NaN. ``time_utc_s`` is POSIX seconds.
    ``source_segment`` is the index of the GPX ``<trkseg>`` (0 for ``<rte>``);
    no per-step calculation may cross a change in this value.
    """

    name: str | None
    kind: TrackKind
    kind_inferred_by: Inference
    elevation_source: ElevationSource
    elevation_source_inferred_by: Inference
    activity_type: str | None
    lat_deg: FloatArray
    lon_deg: FloatArray
    ele_raw_m: FloatArray
    time_utc_s: FloatArray
    source_segment: IntArray

    def __post_init__(self) -> None:
        columns: dict[str, type[np.generic]] = {
            "lat_deg": np.float64,
            "lon_deg": np.float64,
            "ele_raw_m": np.float64,
            "time_utc_s": np.float64,
            "source_segment": np.int32,
        }
        for field_name, dtype in columns.items():
            object.__setattr__(
                self, field_name, _readonly(getattr(self, field_name), dtype)
            )

        lengths = {name: len(getattr(self, name)) for name in columns}
        if len(set(lengths.values())) != 1:
            raise ValueError(f"column lengths differ: {lengths}")

        if np.any(np.isnan(self.lat_deg)) or np.any(np.isnan(self.lon_deg)):
            raise ValueError("coordinates must not be NaN")
        if np.any(np.abs(self.lat_deg) > 90) or np.any(np.abs(self.lon_deg) > 180):
            raise ValueError("coordinates out of range")
        if self.point_count and np.any(np.diff(self.source_segment) < 0):
            raise ValueError("source_segment must be non-decreasing")

    @property
    def point_count(self) -> int:
        return len(self.lat_deg)

    @property
    def source_segment_count(self) -> int:
        return len(np.unique(self.source_segment))

    @property
    def elevation_coverage(self) -> float:
        """Share of points with elevation, 0..1 (0 for an empty track)."""
        if self.point_count == 0:
            return 0.0
        return float(np.mean(~np.isnan(self.ele_raw_m)))

    @property
    def time_coverage(self) -> float:
        """Share of points with a timestamp, 0..1 (0 for an empty track)."""
        if self.point_count == 0:
            return 0.0
        return float(np.mean(~np.isnan(self.time_utc_s)))

    @property
    def has_elevation(self) -> bool:
        return self.point_count > 0 and self.elevation_coverage == 1.0

    @property
    def has_timestamps(self) -> bool:
        return self.point_count > 0 and self.time_coverage == 1.0
