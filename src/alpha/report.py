"""JSON-ready analysis reports (DATA_MODEL.md §6, §9).

Output is deterministic: the same track and params give byte-identical JSON.
Floats are rounded here, at the boundary, never inside calculations.
"""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import fields
from typing import Any

from alpha.domain.analysis import Analysis, TrackSummary
from alpha.domain.climbs import ClimbSection
from alpha.domain.comparison import ComparisonResult
from alpha.domain.quality import QualityWarning
from alpha.domain.track import Track
from alpha.gpx.reader import GpxDocument, SourceTrack

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
