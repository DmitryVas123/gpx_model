"""Terrain slope from a reference elevation model (DATA_MODEL.md §5.8, ADR-015).

Terrain slope is the steepness of the ground *around* a point, independent of
the direction of travel: ``atan(|grad z|)`` from central differences over
``±baseline_m`` north-south and east-west. It is not the track gradient (rise
over the distance actually walked, §5.4): a level traverse across a steep
hillside has a track gradient of 0 % and a large terrain slope.

It describes the ground only. It is not a hazard rating, and ALPHA does not
derive safety statements from it.
"""

import itertools
from dataclasses import dataclass

import numpy as np
from pyproj import Geod

from alpha.domain.geodesy import filtered_step_distances_m
from alpha.domain.track import FloatArray, Track

_GEOD = Geod(ellps="WGS84")

# Upper edges in degrees; the last band is open-ended.
SLOPE_BAND_EDGES_DEG = (10.0, 20.0, 30.0, 40.0, 50.0)


def slope_band_labels() -> tuple[str, ...]:
    edges = SLOPE_BAND_EDGES_DEG
    labels = [f"<{edges[0]:g}"]
    labels += [f"{lo:g}-{hi:g}" for lo, hi in itertools.pairwise(edges)]
    labels.append(f">={edges[-1]:g}")
    return tuple(labels)


@dataclass(frozen=True)
class SlopeProbes:
    """Where to read the model around each point: north, south, east, west."""

    lat_deg: FloatArray  # shape (4, n)
    lon_deg: FloatArray


def slope_probes(
    lat_deg: FloatArray, lon_deg: FloatArray, baseline_m: float
) -> SlopeProbes:
    if baseline_m <= 0:
        raise ValueError("baseline_m must be > 0")
    n = len(lat_deg)
    lats = np.empty((4, n))
    lons = np.empty((4, n))
    for row, azimuth in enumerate((0.0, 180.0, 90.0, 270.0)):
        lon, lat, _ = _GEOD.fwd(
            lon_deg, lat_deg, np.full(n, azimuth), np.full(n, baseline_m)
        )
        lats[row], lons[row] = lat, lon
    return SlopeProbes(lats, lons)


def terrain_slope_deg(probe_values_m: FloatArray, baseline_m: float) -> FloatArray:
    """Slope in degrees from the model values at the probes (rows: N, S, E, W).
    NaN when any probe has no data."""
    north, south, east, west = probe_values_m
    dz_dy = (north - south) / (2 * baseline_m)
    dz_dx = (east - west) / (2 * baseline_m)
    result: FloatArray = np.degrees(np.arctan(np.hypot(dz_dx, dz_dy)))
    return result


@dataclass(frozen=True)
class SlopeBand:
    label: str
    distance_m: float


@dataclass(frozen=True)
class TerrainSlopeSummary:
    baseline_m: float
    bands: tuple[SlopeBand, ...]
    unknown_m: float  # distance where the slope is unknown (no model data)
    median_deg: float | None  # distance-weighted
    p90_deg: float | None
    max_deg: float | None
    max_at_m: float | None  # filtered distance from the start


def summarize_slope(
    track: Track, slope_deg: FloatArray, distance_min_step_m: float, baseline_m: float
) -> TerrainSlopeSummary:
    """Distance-weighted distribution. Each step of the filtered distance
    (ADR-010) counts with the slope at its end point."""
    if len(slope_deg) != track.point_count:
        raise ValueError("one slope value per point is required")
    labels = slope_band_labels()
    if track.point_count < 2:
        bands = tuple(SlopeBand(label, 0.0) for label in labels)
        return TerrainSlopeSummary(baseline_m, bands, 0.0, None, None, None, None)
    steps = filtered_step_distances_m(track, distance_min_step_m)
    end_slope = np.asarray(slope_deg, dtype=np.float64)[1:]
    known = ~np.isnan(end_slope)
    band_index = np.searchsorted(SLOPE_BAND_EDGES_DEG, end_slope[known], side="right")
    distances = np.bincount(band_index, weights=steps[known], minlength=len(labels))
    bands = tuple(
        SlopeBand(label, float(d)) for label, d in zip(labels, distances, strict=True)
    )
    unknown = float(steps[~known].sum())

    weights, values = steps[known], end_slope[known]
    if weights.sum() <= 0:
        return TerrainSlopeSummary(baseline_m, bands, unknown, None, None, None, None)
    order = np.argsort(values)
    cumulative = np.cumsum(weights[order]) / weights.sum()

    def quantile(q: float) -> float:
        return float(values[order][np.searchsorted(cumulative, q)])

    distance = np.concatenate(([0.0], np.cumsum(steps)))
    all_slope = np.asarray(slope_deg, dtype=np.float64)
    worst = int(np.nanargmax(all_slope))
    return TerrainSlopeSummary(
        baseline_m=baseline_m,
        bands=bands,
        unknown_m=unknown,
        median_deg=quantile(0.5),
        p90_deg=quantile(0.9),
        max_deg=float(all_slope[worst]),
        max_at_m=float(distance[worst]),
    )
