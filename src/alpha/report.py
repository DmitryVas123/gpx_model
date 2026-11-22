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

from alpha.application.scenarios import ObservedTime
from alpha.domain.analysis import Analysis, TrackSummary
from alpha.domain.climbs import ClimbSection
from alpha.domain.comparison import ComparisonResult
from alpha.domain.quality import QualityWarning
from alpha.domain.scenarios import ScenarioResult
from alpha.domain.track import Track
from alpha.gpx.reader import GpxDocument, SourceTrack
from alpha.persistence import TrackRecord

# 2: summary gained distance_raw_m; distance_in_stops_m -> distance_while_stopped_m
REPORT_FORMAT_VERSION = "2"

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


def track_report(track: Track, analysis: Analysis) -> dict[str, Any]:
    return {
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
    ref_a: dict[str, Any], ref_b: dict[str, Any], result: ComparisonResult
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
    }


def scenario_report(
    ref: dict[str, Any],
    result: ScenarioResult,
    observed: Sequence[ObservedTime],
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
        "imported_at_utc": _iso(r.imported_at_utc),
        "read_warnings": [warning_report(w) for w in r.read_warnings],
    }


def list_item_report(record: TrackRecord, summary: TrackSummary) -> dict[str, Any]:
    return {"record": record_report(record), "summary": summary_report(summary)}
