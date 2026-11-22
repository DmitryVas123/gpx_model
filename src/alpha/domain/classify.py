"""Heuristics for track kind and elevation source (DATA_MODEL.md §4).

Every guess is returned with ``Inference.HEURISTIC`` so it can be shown as a
guess; when no rule applies the result is ``UNKNOWN`` with ``Inference.DEFAULT``.
The user can always override.
"""

import numpy as np

from alpha.domain.track import ElevationSource, FloatArray, Inference, TrackKind

# Lower-case substrings of a GPX ``creator`` that identify route planners whose
# exports carry elevation from a terrain model. Extend only with verified files.
KNOWN_PLANNER_CREATORS: tuple[str, ...] = ("mapy.com", "mapy.cz")


def infer_kind(time_utc_s: FloatArray) -> tuple[TrackKind, Inference]:
    if len(time_utc_s) == 0:
        return TrackKind.UNKNOWN, Inference.DEFAULT
    present = ~np.isnan(time_utc_s)
    if not present.any():
        return TrackKind.PLANNED, Inference.HEURISTIC
    if present.all() and np.nanmax(time_utc_s) > np.nanmin(time_utc_s):
        return TrackKind.ACTIVITY, Inference.HEURISTIC
    # Partial timestamps, or one constant timestamp (some planners add one).
    return TrackKind.UNKNOWN, Inference.DEFAULT


def infer_elevation_source(
    ele_raw_m: FloatArray, time_utc_s: FloatArray, creator: str | None
) -> tuple[ElevationSource, Inference]:
    present = ele_raw_m[~np.isnan(ele_raw_m)]
    if len(present) == 0:
        return ElevationSource.UNKNOWN, Inference.DEFAULT

    creator_lower = (creator or "").lower()
    if any(planner in creator_lower for planner in KNOWN_PLANNER_CREATORS):
        return ElevationSource.PLANNER_DEM, Inference.HEURISTIC

    no_timestamps = bool(np.all(np.isnan(time_utc_s)))
    integer_only = bool(np.all(present == np.round(present)))
    if no_timestamps and integer_only:
        return ElevationSource.PLANNER_DEM, Inference.HEURISTIC

    return ElevationSource.UNKNOWN, Inference.DEFAULT
