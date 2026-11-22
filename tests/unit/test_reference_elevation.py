from collections.abc import Callable

import numpy as np
import pytest

from alpha.domain.quality import WarningCode
from alpha.domain.reference_elevation import (
    DriftParams,
    compare_elevation,
    interpolate_along_track,
    reference_knots,
    with_elevation,
)
from alpha.domain.track import ElevationSource, FloatArray, Track
from tests.helpers import DEG_PER_METRE, LAT0, line_track, make_track


def test_knots_respect_spacing_and_include_segment_ends() -> None:
    lat = LAT0 + np.arange(0, 101, 1.0) * DEG_PER_METRE  # 100 m, 1 m spacing
    track = make_track(lat, segment=[0] * 50 + [1] * 51)
    knots = reference_knots(track, 20.0)
    assert knots[0] == 0 and knots[-1] == 100
    assert {49, 50} <= set(knots.tolist())  # end and start of the segments
    assert 8 <= len(knots) <= 10


def test_interpolation_is_exact_for_a_linear_series() -> None:
    track = line_track(1.0, 500.0, lambda d: 1000 + 0.1 * d)
    knots = reference_knots(track, 20.0)
    series = interpolate_along_track(track, knots, track.ele_raw_m[knots])
    np.testing.assert_allclose(series, track.ele_raw_m, atol=1e-6)


def test_interpolation_never_crosses_segments() -> None:
    lat = LAT0 + np.array([0.0, 10.0, 20.0, 500.0, 510.0, 520.0]) * DEG_PER_METRE
    track = make_track(lat, segment=[0, 0, 0, 1, 1, 1])
    knots = np.array([0, 2, 3, 5])
    series = interpolate_along_track(track, knots, np.array([0.0, 20.0, 100.0, 120.0]))
    np.testing.assert_allclose(series, [0, 10, 20, 100, 110, 120], atol=1e-6)


def test_nan_knot_propagates_only_to_its_neighbourhood() -> None:
    track = line_track(1.0, 100.0)
    knots = np.array([0, 50, 100])
    series = interpolate_along_track(track, knots, np.array([0.0, np.nan, 100.0]))
    assert np.isnan(series[1:100]).all()
    assert series[0] == 0.0 and series[100] == 100.0


def test_compare_constant_offset() -> None:
    track = line_track(5.0, 2000.0, lambda d: 1000 + 200 * np.sin(d / 300))
    reference = track.ele_raw_m - 7.0  # recorded is 7 m above the reference
    c = compare_elevation(track, reference, 3.0, 3.0)
    assert c.median_difference_m == pytest.approx(7.0)
    assert c.mean_difference_m == pytest.approx(7.0)
    assert c.std_difference_m == pytest.approx(0.0, abs=1e-9)
    assert c.ascent_reference_m == pytest.approx(c.ascent_recorded_m)
    assert c.compared_points == track.point_count
    assert c.reference_coverage == 1.0


def test_compare_locates_largest_difference() -> None:
    track = line_track(10.0, 1000.0, lambda d: 1000 + 0 * d)
    reference = track.ele_raw_m.copy()
    reference[60] += 25.0
    c = compare_elevation(track, reference, 3.0, 0.0)
    assert c.max_abs_difference_m == pytest.approx(25.0)
    assert c.max_abs_difference_at_m == pytest.approx(600.0, abs=2.0)


def test_compare_without_overlap() -> None:
    track = line_track(10.0, 200.0, lambda d: 1000 + 0 * d)
    c = compare_elevation(track, np.full(track.point_count, np.nan), 3.0, 3.0)
    assert c.compared_points == 0
    assert c.median_difference_m is None
    assert c.ascent_reference_m is None
    assert c.ascent_recorded_m is not None


def test_with_elevation_leaves_original_untouched() -> None:
    track = line_track(10.0, 200.0, lambda d: 1000 + 0 * d)
    other = with_elevation(track, track.ele_raw_m + 5)
    assert other.elevation_source is ElevationSource.DEM_ENRICHED
    assert track.ele_raw_m[0] == 1000.0
    assert other.ele_raw_m[0] == 1005.0


def test_invalid_inputs() -> None:
    track = line_track(10.0, 200.0)
    with pytest.raises(ValueError):
        reference_knots(track, 0.0)
    with pytest.raises(ValueError):
        compare_elevation(track, np.zeros(3), 3.0, 3.0)
    with pytest.raises(ValueError):
        interpolate_along_track(track, np.array([0, 1]), np.array([1.0]))


# --- drift of recorded against reference elevation (ADR-014) ---


def drift_case(recorded_minus_reference: Callable[[FloatArray], FloatArray]) -> Track:
    """A 4 km track; the reference is a ramp, recorded = reference + f(d)."""
    return line_track(
        5.0, 4000.0, lambda d: 1000 + 0.1 * d + recorded_minus_reference(d)
    )


def reference_of(track: Track) -> FloatArray:
    return line_track(5.0, 4000.0, lambda d: 1000 + 0.1 * d).ele_raw_m


def test_linear_drift_is_measured_and_warned() -> None:
    track = drift_case(lambda d: -0.01 * d)  # -40 m over 4 km
    c = compare_elevation(track, reference_of(track), 3.0, 3.0)
    assert c.drift is not None
    # Median per 100 m bin, then a rolling median over 5 bins (500 m). The
    # start level is the median of bins 0-2 (bin 1, centre 150 m: -1.5 m);
    # the end level is the median of bins 38-40, where bin 40 holds only the
    # last point at 4000 m (bin 39, centre 3950 m: -39.5 m).
    assert c.drift.start_m == pytest.approx(-1.5, abs=0.1)
    assert c.drift.end_m == pytest.approx(-39.5, abs=0.1)
    assert c.drift.range_m == pytest.approx(38.0, abs=0.2)
    assert c.drift.min_at_m == pytest.approx(3950.0, abs=60)
    (w,) = c.warnings
    assert w.code is WarningCode.ELEVATION_DRIFT
    assert "at km 4.0" in w.message and "38 m range" in w.message


def test_constant_offset_is_not_drift() -> None:
    track = drift_case(lambda d: np.full_like(d, 12.0))
    c = compare_elevation(track, reference_of(track), 3.0, 3.0)
    assert c.drift is not None and c.drift.range_m == pytest.approx(0.0, abs=1e-6)
    assert c.drift.start_m == pytest.approx(12.0) and not c.warnings


def test_short_excursion_is_not_drift() -> None:
    # +30 m for 150 m (like a pressure spike): the 500 m rolling median removes it.
    track = drift_case(lambda d: np.where((d > 2000) & (d < 2150), 30.0, 0.0))
    c = compare_elevation(track, reference_of(track), 3.0, 3.0)
    assert c.max_abs_difference_m == pytest.approx(30.0)
    assert c.drift is not None and c.drift.range_m < 1.0 and not c.warnings


def test_warning_threshold_and_parameters() -> None:
    track = drift_case(lambda d: -0.004 * d)  # -16 m over 4 km: below 20 m
    reference = reference_of(track)
    assert not compare_elevation(track, reference, 3.0, 3.0).warnings
    strict = DriftParams(warn_range_m=10.0)
    assert compare_elevation(track, reference, 3.0, 3.0, strict).warnings
    with pytest.raises(ValueError):
        DriftParams(window_m=50.0)  # window shorter than a bin


def test_drift_needs_overlapping_data() -> None:
    track = drift_case(lambda d: np.zeros_like(d))
    nothing = np.full(track.point_count, np.nan)
    c = compare_elevation(track, nothing, 3.0, 3.0)
    assert c.drift is None and not c.warnings
    one_bin = np.where(np.arange(track.point_count) < 10, reference_of(track), np.nan)
    assert compare_elevation(track, one_bin, 3.0, 3.0).drift is None


def test_drift_ignores_where_the_reference_is_missing() -> None:
    track = drift_case(lambda d: np.where(d > 2000, -50.0, 0.0))
    reference = reference_of(track).copy()
    reference[track.point_count // 2 :] = np.nan  # no reference after 2 km
    c = compare_elevation(track, reference, 3.0, 3.0)
    assert c.drift is not None and c.drift.range_m == pytest.approx(0.0, abs=1e-6)
