"""Distances on the WGS84 ellipsoid (DATA_MODEL.md §5.1, ADR-010)."""

import numpy as np
import numpy.typing as npt
from pyproj import Geod

from alpha.domain.track import FloatArray, Track

_WGS84 = Geod(ellps="WGS84")
EARTH_RADIUS_M = 6_371_008.8  # mean radius; only for small local offsets

IntArray = npt.NDArray[np.intp]
BoolArray = npt.NDArray[np.bool_]


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


def within_segment(track: Track) -> BoolArray:
    """Mask of steps (length n-1) whose two points share a source segment."""
    if track.point_count < 2:
        return np.zeros(0, dtype=np.bool_)
    return np.asarray(track.source_segment[1:] == track.source_segment[:-1])


def local_xyz_m(track: Track) -> tuple[FloatArray, FloatArray, FloatArray]:
    """Equirectangular east/north offsets plus centred elevation, in metres.

    Accurate to well under 1 % over a few kilometres, which is ample for
    comparing short distances against thresholds of a few metres. Elevation is
    zero where the track has incomplete elevation, so 3D checks degrade to 2D.
    """
    if track.point_count == 0:
        empty = np.zeros(0, dtype=np.float64)
        return empty, empty, empty
    lat0 = np.radians(float(np.mean(track.lat_deg)))
    x = EARTH_RADIUS_M * np.cos(lat0) * np.radians(track.lon_deg - track.lon_deg[0])
    y = EARTH_RADIUS_M * np.radians(track.lat_deg - track.lat_deg[0])
    if track.has_elevation:
        z = track.ele_raw_m - float(np.mean(track.ele_raw_m))
    else:
        z = np.zeros(track.point_count)
    return x, y, z


def displacement_pairs(track: Track, min_step_m: float) -> tuple[IntArray, IntArray]:
    """Pairs (from, to) of point indices that make up the jitter-filtered path.

    Within each source segment, the first point is accepted; each later point
    is accepted once it is at least ``min_step_m`` (horizontally) from the last
    accepted point. A remainder shorter than ``min_step_m`` at the end of a
    segment is not counted. With ``min_step_m == 0`` every point is accepted
    and the pairs are exactly the within-segment steps.
    """
    if min_step_m < 0:
        raise ValueError("min_step_m must be >= 0")
    n = track.point_count
    if n < 2:
        empty = np.zeros(0, dtype=np.intp)
        return empty, empty
    if min_step_m == 0:
        steps = np.flatnonzero(within_segment(track))
        return steps.astype(np.intp), (steps + 1).astype(np.intp)

    x, y, _ = local_xyz_m(track)
    xs, ys, seg = x.tolist(), y.tolist(), track.source_segment.tolist()
    limit_sq = min_step_m * min_step_m
    sources: list[int] = []
    targets: list[int] = []
    last = 0
    for i in range(1, n):
        if seg[i] != seg[last]:
            last = i  # new source segment: restart without counting the jump
            continue
        dx, dy = xs[i] - xs[last], ys[i] - ys[last]
        if dx * dx + dy * dy >= limit_sq:
            sources.append(last)
            targets.append(i)
            last = i
    return np.array(sources, dtype=np.intp), np.array(targets, dtype=np.intp)


def filtered_step_distances_m(track: Track, min_step_m: float) -> FloatArray:
    """Jitter-filtered distance credited to each step (length n-1).

    Zero for steps that end on a non-accepted point; for a step ending on an
    accepted point, the geodesic distance from the previous accepted point.
    The sum is the filtered path length; with ``min_step_m == 0`` it equals
    the within-segment sum of :func:`step_distances_m`.
    """
    increments = np.zeros(max(track.point_count - 1, 0), dtype=np.float64)
    src, dst = displacement_pairs(track, min_step_m)
    if len(src):
        increments[dst - 1] = geodesic_distances_m(
            track.lat_deg[src],
            track.lon_deg[src],
            track.lat_deg[dst],
            track.lon_deg[dst],
        )
    return increments
