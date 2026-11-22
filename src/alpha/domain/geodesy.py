"""Step distances on the WGS84 ellipsoid (DATA_MODEL.md §5.1)."""

import numpy as np
from pyproj import Geod

from alpha.domain.track import FloatArray, Track

_WGS84 = Geod(ellps="WGS84")


def geodesic_distances_m(
    lat1_deg: FloatArray,
    lon1_deg: FloatArray,
    lat2_deg: FloatArray,
    lon2_deg: FloatArray,
) -> FloatArray:
    """Horizontal geodesic distance between point pairs, in metres."""
    if len(lat1_deg) == 0:
        return np.zeros(0, dtype=np.float64)
    _, _, distance = _WGS84.inv(lon1_deg, lat1_deg, lon2_deg, lat2_deg)
    return np.asarray(distance, dtype=np.float64)


def step_distances_m(track: Track) -> FloatArray:
    """Distance of every step i -> i+1 (length n-1), including boundary steps.

    Combine with :func:`within_segment` to exclude steps that cross a
    source-segment boundary; those must never be counted as travelled distance.
    """
    if track.point_count < 2:
        return np.zeros(0, dtype=np.float64)
    return geodesic_distances_m(
        track.lat_deg[:-1], track.lon_deg[:-1], track.lat_deg[1:], track.lon_deg[1:]
    )


def within_segment(track: Track) -> np.ndarray[tuple[int], np.dtype[np.bool_]]:
    """Mask of steps (length n-1) whose two points share a source segment."""
    if track.point_count < 2:
        return np.zeros(0, dtype=np.bool_)
    return np.asarray(track.source_segment[1:] == track.source_segment[:-1])
