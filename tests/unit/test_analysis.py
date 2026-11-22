import dataclasses
import json
from typing import Any

import numpy as np
import pytest

from alpha.domain.analysis import (
    ALGORITHM_VERSION,
    AnalysisParams,
    analyse,
    params_hash,
)
from alpha.domain.quality import QualityParams, WarningCode
from alpha.domain.track import Track
from alpha.report import to_json, track_report
from tests.helpers import DEG_PER_METRE, LAT0, line_track, make_track


def hill_track(seconds_per_point: float | None = None) -> Track:
    return line_track(
        5.0,
        4000.0,
        lambda d: 1000 + 300 * np.sin(d / 900) + 2 * np.sin(d / 7),
        seconds_per_point=seconds_per_point,
    )


def with_duplicates(track: Track) -> Track:
    idx = np.repeat(np.arange(track.point_count), 2)
    return dataclasses.replace(
        track,
        lat_deg=track.lat_deg[idx],
        lon_deg=track.lon_deg[idx],
        ele_raw_m=track.ele_raw_m[idx],
        time_utc_s=track.time_utc_s[idx],
        source_segment=track.source_segment[idx],
    )


def reversed_track(track: Track) -> Track:
    return dataclasses.replace(
        track,
        lat_deg=track.lat_deg[::-1],
        lon_deg=track.lon_deg[::-1],
        ele_raw_m=track.ele_raw_m[::-1],
        time_utc_s=np.full(track.point_count, np.nan),
    )


# --- params --------------------------------------------------------------------


def test_params_hash_is_stable_and_sensitive() -> None:
    base = params_hash(AnalysisParams())
    assert base == params_hash(AnalysisParams())
    assert len(base) == 64
    assert params_hash(AnalysisParams(profile_bin_m=25.0)) != base
    nested = AnalysisParams(quality=QualityParams(jump_speed_m_s=8.0))
    assert params_hash(nested) != base


@pytest.mark.parametrize(
    ("name", "value"),
    [("profile_bin_m", 0.0), ("ascent_threshold_m", -1.0), ("moving_window_s", 0.0)],
)
def test_invalid_params_rejected(name: str, value: float) -> None:
    changes: dict[str, Any] = {name: value}
    with pytest.raises(ValueError, match=name):
        dataclasses.replace(AnalysisParams(), **changes)


# --- analysis ------------------------------------------------------------------


def test_analysis_records_version_and_params() -> None:
    params = AnalysisParams(profile_bin_m=100.0)
    a = analyse(hill_track(), params)
    assert a.algorithm_version == ALGORITHM_VERSION
    assert a.params == params
    assert a.params_hash == params_hash(params)
    assert a.profile[0].length_m == pytest.approx(100.0)


def test_planned_route_time_metrics_are_unknown() -> None:
    s = analyse(hill_track()).summary
    assert s.ascent_m is not None and s.ascent_m > 0
    for name in (
        "elapsed_time_s",
        "moving_time_s",
        "stopped_time_s",
        "recording_gap_s",
        "stop_count",
        "distance_while_stopped_m",
    ):
        assert getattr(s, name) is None, name


def test_track_without_elevation() -> None:
    s = analyse(line_track(10.0, 500.0)).summary
    assert s.distance_m == pytest.approx(500.0, rel=0.01)
    assert s.distance_3d_m is None
    assert s.ascent_m is None
    assert s.max_elevation_m is None


def test_distance_excludes_segment_boundaries() -> None:
    lat = LAT0 + np.array([0.0, 100.0, 1000.0, 1100.0]) * DEG_PER_METRE
    track = make_track(lat, ele=[0.0, 0.0, 0.0, 0.0], segment=[0, 0, 1, 1])
    s = analyse(track).summary
    assert s.distance_m == pytest.approx(200.0, rel=0.01)
    assert s.distance_3d_m == pytest.approx(s.distance_m)


def test_distance_3d_is_at_least_distance() -> None:
    s = analyse(hill_track()).summary
    assert s.distance_3d_m is not None
    assert s.distance_3d_m > s.distance_m


def test_duplicate_points_change_nothing() -> None:
    track = hill_track(seconds_per_point=3.0)
    a, b = analyse(track).summary, analyse(with_duplicates(track)).summary
    assert b.distance_m == pytest.approx(a.distance_m)
    assert b.ascent_m == pytest.approx(a.ascent_m)
    assert b.descent_m == pytest.approx(a.descent_m)
    assert b.moving_time_s == pytest.approx(a.moving_time_s)


def test_reversal_swaps_ascent_and_descent() -> None:
    track = hill_track()
    a, b = analyse(track), analyse(reversed_track(track))
    assert b.summary.distance_m == pytest.approx(a.summary.distance_m)
    tolerance = 2 * a.params.ascent_threshold_m
    assert b.summary.ascent_m == pytest.approx(a.summary.descent_m, abs=tolerance)
    assert b.summary.descent_m == pytest.approx(a.summary.ascent_m, abs=tolerance)


def test_net_elevation_invariant() -> None:
    track = hill_track()
    s = analyse(track).summary
    assert s.ascent_m is not None and s.descent_m is not None
    net = track.ele_raw_m[-1] - track.ele_raw_m[0]
    assert abs((s.ascent_m - s.descent_m) - net) <= 2 * 3.0


def test_point_density_changes_little() -> None:
    def hill(
        d: np.ndarray[tuple[int], np.dtype[np.float64]],
    ) -> np.ndarray[tuple[int], np.dtype[np.float64]]:
        return 1000 + 300 * np.sin(d / 900)

    # 3000 m is a whole number of both spacings, so both tracks end at the same point.
    dense = analyse(line_track(1.0, 3000.0, hill)).summary
    sparse = analyse(line_track(15.0, 3000.0, hill)).summary
    assert sparse.distance_m == pytest.approx(dense.distance_m, rel=1e-3)
    assert sparse.ascent_m == pytest.approx(dense.ascent_m, rel=0.01)
    assert sparse.descent_m == pytest.approx(dense.descent_m, rel=0.01)


def test_climbs_found_on_hill() -> None:
    climbs = analyse(hill_track()).climbs
    assert [c.direction for c in climbs] == ["up", "down"]


def test_gradient_outlier_warning() -> None:
    lat = LAT0 + np.arange(0, 201, 10) * DEG_PER_METRE
    ele = [0.0] * 10 + [200.0] * 11  # +200 m in the step from 90 m to 100 m
    a = analyse(make_track(lat, ele=ele))
    (w,) = [w for w in a.warnings if w.code is WarningCode.GRADIENT_OUTLIER]
    assert w.bin_indices == (1,)  # the 50..100 m bin
    assert a.profile[1].gradient_percent == pytest.approx(400.0, rel=0.05)


# --- determinism ---------------------------------------------------------------


def test_json_report_is_byte_identical() -> None:
    track = hill_track(seconds_per_point=2.0)
    first = to_json(track_report(track, analyse(track)))
    second = to_json(track_report(track, analyse(track)))
    assert first == second
    data = json.loads(first)
    assert data["summary"]["moving_time_s"]["provenance"] == "calculated"


def test_unknown_values_are_labelled() -> None:
    track = hill_track()
    data = json.loads(to_json(track_report(track, analyse(track))))
    moving = data["summary"]["moving_time_s"]
    assert moving == {"value": None, "unit": "s", "provenance": "unknown"}
    assert "NaN" not in to_json(track_report(track, analyse(track)))


def test_raw_distance_is_kept_next_to_filtered_distance() -> None:
    rng = np.random.default_rng(3)
    # 600 s standing with +-1 m jitter, then 300 m walk at 1 m/s. Jitter whose
    # spread exceeds distance_min_step_m can leak through (ADR-010).
    pos = np.concatenate((rng.uniform(-1.0, 1.0, 600), np.arange(1.0, 301.0)))
    track = make_track(
        LAT0 + pos * DEG_PER_METRE, ele=[1000.0] * len(pos), time=np.arange(len(pos))
    )
    s = analyse(track).summary
    assert s.distance_raw_m > s.distance_m + 300.0  # jitter inflates raw length
    assert s.distance_m == pytest.approx(300.0, abs=10.0)


def test_profile_adds_up_to_filtered_distance() -> None:
    track = hill_track(seconds_per_point=1.0)
    a = analyse(track)
    assert a.profile[-1].end_distance_m == pytest.approx(a.summary.distance_m)
    assert sum(a.gradient_distribution.values()) == pytest.approx(a.summary.distance_m)


def test_distance_filter_can_be_switched_off() -> None:
    track = hill_track()
    s = analyse(track, AnalysisParams(distance_min_step_m=0.0)).summary
    assert s.distance_m == pytest.approx(s.distance_raw_m)
