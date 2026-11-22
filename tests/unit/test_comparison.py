import dataclasses

import numpy as np
import pytest

from alpha.domain.analysis import AnalysisParams, analyse
from alpha.domain.comparison import ComparisonResult, MetricDelta, compare
from alpha.domain.track import ElevationSource, Track, TrackKind
from tests.helpers import line_track


def hill(length_m: float, height_m: float, seconds: float | None = None) -> Track:
    return line_track(
        5.0,
        length_m,
        lambda d: 1000 + height_m * np.sin(np.pi * d / length_m),
        seconds_per_point=seconds,
    )


def metric(result: ComparisonResult, name: str) -> MetricDelta:
    return next(d for d in result.metrics if d.metric == name)


def test_identical_tracks_have_zero_deltas() -> None:
    t = hill(4000, 400)
    a = analyse(t)
    r = compare(t, a, t, a)
    for d in r.metrics:
        if d.a is not None:
            assert d.delta == 0.0
            assert d.material is False
    assert all(b.delta_m == 0.0 for b in r.bands)


def test_deltas_and_material_flags() -> None:
    small, big = hill(4000, 400), hill(6000, 900)
    r = compare(small, analyse(small), big, analyse(big))
    distance = metric(r, "distance_m")
    assert distance.delta == pytest.approx(2000, abs=10)
    assert distance.ratio == pytest.approx(1.5, rel=0.01)
    assert distance.material is True
    ascent = metric(r, "ascent_m")
    assert ascent.delta == pytest.approx(500, abs=10)
    assert ascent.material is True
    assert metric(r, "max_elevation_m").material is True  # +500 m >= 200 m


def test_small_differences_are_not_material() -> None:
    a_track, b_track = hill(4000, 400), hill(4400, 420)
    r = compare(a_track, analyse(a_track), b_track, analyse(b_track))
    assert metric(r, "distance_m").material is False
    assert metric(r, "max_elevation_m").material is False


def test_unknown_values_stay_unknown() -> None:
    planned = dataclasses.replace(hill(4000, 400), kind=TrackKind.PLANNED)
    activity = dataclasses.replace(
        hill(4000, 400, seconds=4.0), kind=TrackKind.ACTIVITY
    )
    r = compare(planned, analyse(planned), activity, analyse(activity))
    moving = metric(r, "moving_time_s")
    assert moving.a is None and moving.b is not None
    assert (moving.delta, moving.ratio, moving.material) == (None, None, None)
    assert any("A: time metrics are unknown (planned route)" in n for n in r.notes)
    assert any("an activity track" in n for n in r.notes)


def test_zero_baseline_has_no_ratio() -> None:
    up = line_track(5.0, 2000.0, lambda d: 1000 + 0.1 * d)
    r = compare(up, analyse(up), up, analyse(up))
    descent = metric(r, "descent_m")
    assert descent.a == 0.0
    assert descent.ratio is None
    assert descent.material is None


def test_band_totals_match_distances() -> None:
    a_track, b_track = hill(4000, 400), hill(6000, 900)
    r = compare(a_track, analyse(a_track), b_track, analyse(b_track))
    assert sum(b.a_m for b in r.bands) == pytest.approx(metric(r, "distance_m").a)
    assert sum(b.b_m for b in r.bands) == pytest.approx(metric(r, "distance_m").b)


def test_climbs_are_reported() -> None:
    a_track, b_track = hill(4000, 400), hill(6000, 900)
    r = compare(a_track, analyse(a_track), b_track, analyse(b_track))
    assert r.biggest_climb_a is not None and r.biggest_climb_b is not None
    assert r.biggest_climb_b.elevation_change_m > r.biggest_climb_a.elevation_change_m
    flat = line_track(5.0, 2000.0, lambda d: d * 0)
    r_flat = compare(flat, analyse(flat), a_track, analyse(a_track))
    assert r_flat.biggest_climb_a is None
    assert r_flat.steepest_climb_a is None


def test_elevation_source_difference_is_noted() -> None:
    t = hill(4000, 400)
    other = dataclasses.replace(t, elevation_source=ElevationSource.PLANNER_DEM)
    r = compare(t, analyse(t), other, analyse(other))
    assert any("Elevation sources differ" in n for n in r.notes)


def test_safety_note_is_always_present() -> None:
    t = hill(4000, 400)
    r = compare(t, analyse(t), t, analyse(t))
    assert any("nothing about conditions" in n for n in r.notes)


def test_different_params_are_rejected() -> None:
    t = hill(4000, 400)
    with pytest.raises(ValueError, match="not comparable"):
        compare(t, analyse(t), t, analyse(t, AnalysisParams(profile_bin_m=25)))
