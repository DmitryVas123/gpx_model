"""JSON-ready analysis reports (DATA_MODEL.md §6, §9).

Output is deterministic: the same track and params give byte-identical JSON.
Floats are rounded here, at the boundary, never inside calculations.
"""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import fields
from datetime import UTC, datetime
from typing import Any

from alpha.application.baseline import BaselineView, PersonalAssumptions
from alpha.application.elevation import ElevationView
from alpha.application.profiles import Profile
from alpha.application.scenarios import ObservedTime
from alpha.domain.analysis import (
    ALGORITHM_VERSION,
    Analysis,
    TrackSummary,
    params_hash,
)
from alpha.domain.climbs import ClimbSection
from alpha.domain.comparison import ComparisonResult
from alpha.domain.quality import QualityWarning
from alpha.domain.reference_elevation import ElevationDrift
from alpha.domain.scenarios import DinAssumptions, ScenarioResult
from alpha.domain.terrain import TerrainSlopeSummary
from alpha.domain.track import Track
from alpha.explain import Explanation
from alpha.gpx.reader import GpxDocument, SourceTrack
from alpha.modelling import SampleResult, Validation
from alpha.persistence import DemProfileRecord, TrackRecord

# 2: summary gained distance_raw_m; distance_in_stops_m -> distance_while_stopped_m
# 3: "elevation_series" (recorded or DEM, with provenance and attribution)
# 4: elevation check report; list items gained "dem_datasets"
# 5: elevation check gained "comparison.drift" and "warnings" (ELEVATION_DRIFT)
# 6: elevation check gained "terrain_slope" and "terrain_slope_note"
# 7: personal baseline report (Phase 7)
# 8: baseline report holds two models ("models": multiplier, split)
# 9: track records gained "effort"; baseline report gained "effort"
# 10: scenario report gained "personal" (fitted assumptions, ADR-019)
# 11: explanation report (ADR-020)
REPORT_FORMAT_VERSION = "12"


def elevation_series_report(dem: DemProfileRecord | None) -> dict[str, Any]:
    """Which elevation the elevation-dependent values use. DEM provenance and
    the dataset's attribution travel with every result derived from it."""
    if dem is None:  # same keys either way, so CLI and API JSON are identical
        return {
            "source": "recorded",
            **dict.fromkeys(
                (
                    "dataset_id",
                    "dataset_name",
                    "resolution_m",
                    "surface",
                    "vertical_datum",
                    "sample_spacing_m",
                    "retrieved_at_utc",
                    "attribution",
                )
            ),
        }
    return {
        "source": "dem",
        "dataset_id": dem.dataset_id,
        "dataset_name": dem.dataset_name,
        "resolution_m": dem.resolution_m,
        "surface": dem.surface,
        "vertical_datum": dem.vertical_datum,
        "sample_spacing_m": dem.sample_spacing_m,
        "retrieved_at_utc": dem.retrieved_at_utc.isoformat(),
        "attribution": dem.attribution,
    }


# Summary field -> (unit, decimals). Every summary value is calculated from the
# GPX; a missing value is reported as "unknown", never as zero.
SUMMARY_UNITS: dict[str, tuple[str, int]] = {
    "distance_m": ("m", 1),
    "distance_raw_m": ("m", 1),
    "distance_3d_m": ("m", 1),
    "ascent_m": ("m", 1),
    "descent_m": ("m", 1),
    "min_elevation_m": ("m", 1),
    "max_elevation_m": ("m", 1),
    "elapsed_time_s": ("s", 1),
    "moving_time_s": ("s", 1),
    "stopped_time_s": ("s", 1),
    "recording_gap_s": ("s", 1),
    "stop_count": ("count", 0),
    "distance_while_stopped_m": ("m", 1),
}


def _round(value: float | None, decimals: int) -> float | int | None:
    if value is None:
        return None
    if decimals == 0:
        return round(value)
    rounded = round(float(value), decimals)
    return 0.0 if rounded == 0 else rounded  # avoid "-0.0"


def summary_report(summary: TrackSummary) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for f in fields(summary):
        unit, decimals = SUMMARY_UNITS[f.name]
        value = getattr(summary, f.name)
        result[f.name] = {
            "value": _round(value, decimals),
            "unit": unit,
            "provenance": "unknown" if value is None else "calculated",
        }
    return result


def warning_report(w: QualityWarning) -> dict[str, Any]:
    report: dict[str, Any] = {"code": str(w.code), "message": w.message}
    if w.count is not None:
        report["count"] = w.count
    if w.point_indices:
        report["point_indices"] = list(w.point_indices)
    if w.bin_indices:
        report["bin_indices"] = list(w.bin_indices)
    return report


def track_report(
    track: Track, analysis: Analysis, dem: DemProfileRecord | None = None
) -> dict[str, Any]:
    return {
        "elevation_series": elevation_series_report(dem),
        "track": {
            "name": track.name,
            "activity_type": track.activity_type,
            "kind": {
                "value": str(track.kind),
                "inferred_by": str(track.kind_inferred_by),
            },
            "elevation_source": {
                "value": str(track.elevation_source),
                "inferred_by": str(track.elevation_source_inferred_by),
            },
            "point_count": track.point_count,
            "source_segment_count": track.source_segment_count,
            "elevation_coverage": _round(track.elevation_coverage, 4),
            "time_coverage": _round(track.time_coverage, 4),
        },
        "analysis": {
            "algorithm_version": analysis.algorithm_version,
            "params_hash": analysis.params_hash,
            "params": _params(analysis),
        },
        "summary": summary_report(analysis.summary),
        "gradient_distribution_m": {
            band: _round(metres, 1)
            for band, metres in analysis.gradient_distribution.items()
        },
        "climb_sections": [_climb_report(c) for c in analysis.climbs],
        "profile": [
            {
                "index": b.index,
                "start_distance_m": _round(b.start_distance_m, 1),
                "end_distance_m": _round(b.end_distance_m, 1),
                "ele_start_m": _round(b.ele_start_m, 1),
                "ele_end_m": _round(b.ele_end_m, 1),
                "ele_change_m": _round(b.ele_change_m, 2),
                "gradient_percent": _round(b.gradient_percent, 2),
                "elapsed_s": _round(b.elapsed_s, 1),
            }
            for b in analysis.profile
        ],
        "warnings": [warning_report(w) for w in analysis.warnings],
    }


def _params(analysis: Analysis) -> dict[str, Any]:
    p = analysis.params
    result: dict[str, Any] = {
        f.name: getattr(p, f.name) for f in fields(p) if f.name != "quality"
    }
    result["quality"] = {f.name: getattr(p.quality, f.name) for f in fields(p.quality)}
    return result


def file_report(
    file_name: str,
    data: bytes,
    doc: GpxDocument,
    results: Sequence[tuple[SourceTrack, Analysis]],
) -> dict[str, Any]:
    """Full report for one GPX file: file facts, then one entry per track."""
    return {
        "format_version": REPORT_FORMAT_VERSION,
        "file": {
            "name": file_name,
            "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
            "creator": doc.creator,
            "gpx_version": doc.gpx_version,
            "waypoint_count": doc.waypoint_count,
        },
        "warnings": [warning_report(w) for w in doc.warnings],
        "tracks": [
            {
                "source": {
                    "element": str(source.element),
                    "index": source.index_in_element,
                },
                "read_warnings": [warning_report(w) for w in source.read_warnings],
                **track_report(source.track, analysis),
            }
            for source, analysis in results
        ],
    }


def to_json(report: dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def _climb_report(c: ClimbSection | None) -> dict[str, Any] | None:
    if c is None:
        return None
    return {
        "direction": str(c.direction),
        "start_distance_m": _round(c.start_distance_m, 1),
        "end_distance_m": _round(c.end_distance_m, 1),
        "elevation_change_m": _round(c.elevation_change_m, 1),
        "mean_gradient_percent": _round(c.mean_gradient_percent, 2),
        "max_bin_gradient_percent": _round(c.max_bin_gradient_percent, 2),
        "start_bin": c.start_bin,
        "end_bin": c.end_bin,
    }


def stored_track_ref(
    track_id: int, source_filename: str, planned_track_id: int | None
) -> dict[str, Any]:
    return {
        "id": track_id,
        "source_filename": source_filename,
        "planned_track_id": planned_track_id,
    }


def comparison_report(
    ref_a: dict[str, Any],
    ref_b: dict[str, Any],
    result: ComparisonResult,
    dem_a: DemProfileRecord | None = None,
    dem_b: DemProfileRecord | None = None,
) -> dict[str, Any]:
    """B compared against A. Unknown values are null, never zero."""

    def decimals(metric: str) -> int:
        return SUMMARY_UNITS[metric][1]

    return {
        "format_version": REPORT_FORMAT_VERSION,
        "a": ref_a,
        "b": ref_b,
        "analysis": {
            "algorithm_version": result.algorithm_version,
            "params_hash": result.params_hash,
        },
        "metrics": [
            {
                "metric": d.metric,
                "unit": SUMMARY_UNITS[d.metric][0],
                "a": _round(d.a, decimals(d.metric)),
                "b": _round(d.b, decimals(d.metric)),
                "delta": _round(d.delta, decimals(d.metric)),
                "ratio": _round(d.ratio, 4),
                "material": d.material,
            }
            for d in result.metrics
        ],
        "gradient_bands_m": [
            {
                "band": b.band,
                "a": _round(b.a_m, 1),
                "b": _round(b.b_m, 1),
                "delta": _round(b.delta_m, 1),
                "material": b.material,
            }
            for b in result.bands
        ],
        "biggest_climb": {
            "a": _climb_report(result.biggest_climb_a),
            "b": _climb_report(result.biggest_climb_b),
        },
        "steepest_climb": {
            "a": _climb_report(result.steepest_climb_a),
            "b": _climb_report(result.steepest_climb_b),
        },
        "notes": list(result.notes),
        "elevation_series": {
            "a": elevation_series_report(dem_a),
            "b": elevation_series_report(dem_b),
        },
    }


def scenario_report(
    ref: dict[str, Any],
    result: ScenarioResult,
    observed: Sequence[ObservedTime],
    dem: DemProfileRecord | None = None,
    personal: PersonalAssumptions | None = None,
) -> dict[str, Any]:
    """Scenario estimates with their assumptions, plus observed times."""
    a = result.assumptions
    return {
        "format_version": REPORT_FORMAT_VERSION,
        "track": ref,
        "provenance": "estimated",
        "method": {"name": result.method, "version": result.method_version},
        "analysis": {
            "algorithm_version": result.algorithm_version,
            "params_hash": result.params_hash,
        },
        "assumptions": {
            "source": str(a.source),
            "label": a.label,
            "horizontal_speed_m_h": a.horizontal_speed_m_h,
            "ascent_rate_m_h": a.ascent_rate_m_h,
            "descent_rate_m_h": a.descent_rate_m_h,
            "break_s": a.break_s,
            "delay_s": a.delay_s,
        },
        "inputs": {
            "provenance": "calculated",
            "distance_m": _round(result.inputs.distance_m, 1),
            "ascent_m": _round(result.inputs.ascent_m, 1),
            "descent_m": _round(result.inputs.descent_m, 1),
        },
        "scenarios": [
            {
                "label": s.label,
                "speed_multiplier": s.speed_multiplier,
                "estimated_duration_s": _round(s.estimated_duration_s, 0),
                "breakdown_s": {
                    "horizontal": _round(s.breakdown.horizontal_s, 0),
                    "vertical": _round(s.breakdown.vertical_s, 0),
                    "moving": _round(s.breakdown.moving_s, 0),
                    "break": _round(s.breakdown.break_s, 0),
                    "delay": _round(s.breakdown.delay_s, 0),
                },
            }
            for s in result.scenarios
        ],
        "observed": [
            {
                "provenance": "observed",
                "track_id": o.track_id,
                "name": o.name,
                "relation": str(o.relation),
                "moving_time_s": _round(o.moving_time_s, 0),
                "elapsed_time_s": _round(o.elapsed_time_s, 0),
                "implied_speed_multiplier": _round(o.implied_speed_multiplier, 3),
            }
            for o in observed
        ],
        "uncertainty_notes": list(result.uncertainty_notes),
        "elevation_series": elevation_series_report(dem),
        "personal": _personal_report(personal),
    }


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).isoformat()


def record_report(r: TrackRecord) -> dict[str, Any]:
    """Stored track metadata (no points, no coordinates)."""
    return {
        "id": r.id,
        "source_file_id": r.source_file_id,
        "source_filename": r.source_filename,
        "gpx_element": r.gpx_element,
        "index_in_element": r.index_in_element,
        "name": r.name,
        "activity_type": r.activity_type,
        "kind": {"value": str(r.kind), "inferred_by": str(r.kind_inferred_by)},
        "elevation_source": {
            "value": str(r.elevation_source),
            "inferred_by": str(r.elevation_source_inferred_by),
        },
        "planned_track_id": r.planned_track_id,
        "point_count": r.point_count,
        "has_elevation": r.has_elevation,
        "has_timestamps": r.has_timestamps,
        "started_at_utc": _iso(r.started_at_utc),
        "effort": None if r.effort is None else str(r.effort),
        "imported_at_utc": _iso(r.imported_at_utc),
        "read_warnings": [warning_report(w) for w in r.read_warnings],
    }


def list_item_report(
    record: TrackRecord, summary: TrackSummary, dem_datasets: Sequence[str] = ()
) -> dict[str, Any]:
    return {
        "record": record_report(record),
        "summary": summary_report(summary),
        "dem_datasets": list(dem_datasets),
    }


def elevation_check_report(ref: dict[str, Any], view: ElevationView) -> dict[str, Any]:
    """Recorded elevation compared with a stored DEM profile (ADR-012)."""
    p, c = view.profile, view.comparison
    return {
        "format_version": REPORT_FORMAT_VERSION,
        "track": ref,
        "elevation_series": elevation_series_report(p),
        "tiles": list(p.tiles),
        "downloaded_tiles": list(view.downloaded_tiles),
        "reused_stored_profile": view.reused,
        "comparison": {
            "provenance": "calculated",
            "difference": "recorded minus DEM",
            "compared_points": c.compared_points,
            "reference_coverage": _round(c.reference_coverage, 4),
            "median_difference_m": _round(c.median_difference_m, 1),
            "mean_difference_m": _round(c.mean_difference_m, 1),
            "std_difference_m": _round(c.std_difference_m, 1),
            "p95_abs_difference_m": _round(c.p95_abs_difference_m, 1),
            "max_abs_difference_m": _round(c.max_abs_difference_m, 1),
            "max_abs_difference_at_m": _round(c.max_abs_difference_at_m, 0),
            "ascent_recorded_m": _round(c.ascent_recorded_m, 1),
            "descent_recorded_m": _round(c.descent_recorded_m, 1),
            "ascent_dem_m": _round(c.ascent_reference_m, 1),
            "descent_dem_m": _round(c.descent_reference_m, 1),
            "drift": _drift_report(c.drift),
        },
        "warnings": [warning_report(w) for w in c.warnings],
        "terrain_slope": _slope_report(view.slope),
        "terrain_slope_note": view.slope_note,
    }


def _slope_report(s: TerrainSlopeSummary | None) -> dict[str, Any] | None:
    if s is None:
        return None
    return {
        "provenance": "calculated",
        "meaning": "steepness of the ground around the track, not the track "
        "gradient and not a hazard rating",
        "baseline_m": s.baseline_m,
        "bands_m": [
            {"band_deg": b.label, "distance_m": _round(b.distance_m, 0)}
            for b in s.bands
        ],
        "unknown_m": _round(s.unknown_m, 0),
        "median_deg": _round(s.median_deg, 1),
        "p90_deg": _round(s.p90_deg, 1),
        "max_deg": _round(s.max_deg, 1),
        "max_at_m": _round(s.max_at_m, 0),
    }


def _drift_report(d: ElevationDrift | None) -> dict[str, Any] | None:
    if d is None:
        return None
    return {
        "range_m": _round(d.range_m, 1),
        "start_m": _round(d.start_m, 1),
        "end_m": _round(d.end_m, 1),
        "min_m": _round(d.min_m, 1),
        "min_at_m": _round(d.min_at_m, 0),
        "max_m": _round(d.max_m, 1),
        "max_at_m": _round(d.max_at_m, 0),
        "window_m": d.window_m,
    }


def baseline_report(view: BaselineView) -> dict[str, Any]:
    """Personal speed models on DIN 33466 (ADR-016, ADR-017). Per-activity
    ratios are observed; factors, intervals, and errors are fitted."""
    f, s, bp = view.fit, view.split, view.baseline_params
    ref = f.reference

    def interval(i: tuple[float, float] | None) -> list[float] | None:
        return None if i is None else [round(i[0], 3), round(i[1], 3)]

    def assumptions(a: DinAssumptions | None) -> dict[str, Any] | None:
        if a is None:
            return None
        return {
            "source": str(a.source),
            "label": a.label,
            "horizontal_speed_m_h": round(a.horizontal_speed_m_h, 1),
            "ascent_rate_m_h": round(a.ascent_rate_m_h, 1),
            "descent_rate_m_h": round(a.descent_rate_m_h, 1),
        }

    def validation(v: Validation | None) -> dict[str, Any] | None:
        if v is None:
            return None
        return {
            "method": "leave one route out",
            "folds": v.folds,
            "median_abs_error": round(v.median_abs_error, 4),
            "max_abs_error": round(v.max_abs_error, 4),
        }

    multiplier = {
        "status": str(f.status),
        "min_routes": f.min_groups,
        "provenance": "fitted" if f.multiplier is not None else None,
        "speed_multiplier": _round(f.multiplier, 3),
        "spread_log": _round(f.spread, 4),
        "average_interval_95": interval(f.average_interval),
        "single_activity_interval_95": interval(f.single_interval),
        "assumptions": assumptions(f.assumptions),
        "validation": validation(f.validation),
    }
    split = {
        "status": str(s.status),
        "reason": s.reason,
        "min_routes": s.min_groups,
        "ratio_range": _round(s.ratio_range, 3),
        "min_ratio_range": s.min_ratio_range,
        "provenance": "fitted" if s.horizontal_factor is not None else None,
        "horizontal_factor": _round(s.horizontal_factor, 3),
        "vertical_factor": _round(s.vertical_factor, 3),
        "horizontal_interval_95": interval(s.horizontal_interval),
        "vertical_interval_95": interval(s.vertical_interval),
        "residual_spread_log": _round(s.residual_spread, 4),
        "assumptions": assumptions(s.assumptions),
        "validation": validation(s.validation),
    }
    split_by_track = {r.sample.track_id: r for r in s.samples}

    def activity(r: SampleResult) -> dict[str, Any]:
        x = r.sample
        sp = split_by_track[x.track_id]
        return {
            "track_id": x.track_id,
            "name": x.name,
            "route": x.group,
            "started_at_utc": _iso(x.started_at_utc),
            "distance_m": _round(x.distance_m, 1),
            "ascent_m": _round(x.ascent_m, 1),
            "descent_m": _round(x.descent_m, 1),
            "moving_time_s": _round(x.moving_time_s, 0),
            "effort": str(x.effort) if x.effort else None,
            "reference_moving_s": _round(r.reference_moving_s, 0),
            "vertical_horizontal_ratio": _round(
                sp.reference_vertical_s / sp.reference_horizontal_s, 3
            ),
            "implied_speed_multiplier": round(r.implied_multiplier, 3),
            "loo_error_multiplier": _round(r.error_ratio, 4),
            "loo_error_split": _round(sp.error_ratio, 4),
        }

    return {
        "format_version": REPORT_FORMAT_VERSION,
        "method": "din33466",
        "elevation": view.params.elevation,
        "analysis": {
            "algorithm_version": ALGORITHM_VERSION,
            "params_hash": params_hash(view.params),
        },
        "reference": {
            "label": ref.label,
            "horizontal_speed_m_h": ref.horizontal_speed_m_h,
            "ascent_rate_m_h": ref.ascent_rate_m_h,
            "descent_rate_m_h": ref.descent_rate_m_h,
        },
        "requirements": {
            "min_moving_s": bp.min_moving_s,
            "activity_types": list(bp.activity_types),
        },
        "n_activities": f.n_activities,
        "n_routes": f.n_groups,
        # Generated activities among the fitted ones (synthetic profile, ADR-021).
        "synthetic_data": view.synthetic_data,
        "synthetic_track_ids": list(view.synthetic_track_ids),
        "models": {"multiplier": multiplier, "split": split},
        "effort": {
            "fitted_on": bp.effort,
            "included": [str(e) if e else "not stated" for e in bp.efforts],
            "observed_by_effort": [
                {
                    "effort": str(g.effort) if g.effort else "not stated",
                    "n_activities": g.n_activities,
                    "provenance": "observed",
                    "mean_implied_multiplier": round(g.mean_implied_multiplier, 3),
                }
                for g in view.effort_groups
            ],
            "outside_fit": [
                {
                    "track_id": p.sample.track_id,
                    "name": p.sample.name,
                    "effort": str(p.sample.effort) if p.sample.effort else None,
                    "pace_vs_multiplier": _round(p.vs_multiplier, 3),
                    "pace_vs_split": _round(p.vs_split, 3),
                }
                for p in view.outside_effort
            ],
        },
        "activities": [activity(r) for r in f.samples],
        "excluded": [
            {"track_id": e.track_id, "name": e.name, "reason": e.reason}
            for e in view.excluded
        ],
    }


def _personal_report(p: PersonalAssumptions | None) -> dict[str, Any] | None:
    """What fitted assumptions rest on (ADR-019); null for preset/user ones."""
    if p is None:
        return None
    return {
        "provenance": "fitted",
        "model": p.model,
        "n_activities": p.n_activities,
        "n_routes": p.n_routes,
        "effort": p.effort,
        "loo_median_abs_error": round(p.median_abs_error, 4),
        "range_multipliers": [round(k, 4) for k in p.range_multipliers],
        "range_meaning": "95 % range for a single new hike (own variation)",
        "synthetic_data": p.synthetic_data,
    }


def explanation_report(e: Explanation, notes: Sequence[str] = ()) -> dict[str, Any]:
    """A plain-language explanation and the facts it may use (ADR-020)."""
    return {
        "format_version": REPORT_FORMAT_VERSION,
        "topic": e.topic,
        "source": e.source,
        "model": e.model,
        "statements": [
            {"text": s.text, "facts": list(s.facts), "caveat": s.caveat}
            for s in e.statements
        ],
        "boundary": e.boundary,
        "fallback_reasons": list(e.fallback_reasons),
        "notes": list(notes),
        "facts": [
            {
                "key": f.key,
                "label": f.label,
                "display": f.display(),
                "provenance": f.provenance,
            }
            for f in (e.facts or ())
        ],
    }


def profiles_report(profiles: Sequence[Profile], active: Profile) -> dict[str, Any]:
    """Local profiles (ADR-021). Paths are not reported: the page needs only
    names, and the folder layout stays on this computer."""
    return {
        "active": active.name,
        "profiles": [
            {"name": p.name, "synthetic_data": p.synthetic, "active": p == active}
            for p in profiles
        ],
    }
