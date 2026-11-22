"""Command-line entry point. No calculations here; only reading and printing."""

import argparse
import hashlib
import io
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from alpha.domain.analysis import Analysis, AnalysisParams, analyse
from alpha.domain.profile import GRADIENT_BANDS, UNKNOWN_BAND
from alpha.domain.quality import QualityWarning, check_track
from alpha.gpx.reader import GpxDocument, GpxReadError, read_gpx
from alpha.report import file_report, to_json

EXIT_OK = 0
EXIT_READ_ERROR = 2
EXIT_BAD_PARAMS = 3


def main(argv: Sequence[str] | None = None, out: TextIO | None = None) -> int:
    stream = out or _utf8_stdout()
    parser = argparse.ArgumentParser(prog="alpha", description="ALPHA GPX analysis")
    commands = parser.add_subparsers(dest="command", required=True)

    inspect = commands.add_parser(
        "inspect", help="list tracks, inferred kind, and data-quality warnings"
    )
    inspect.add_argument("file", type=Path)

    analyse_cmd = commands.add_parser(
        "analyse", help="distance, ascent, time, gradient profile, climb sections"
    )
    analyse_cmd.add_argument("file", type=Path)
    analyse_cmd.add_argument(
        "--json", action="store_true", help="print the full report as JSON"
    )
    defaults = AnalysisParams()
    for option, attr, unit in (
        ("--profile-bin-m", "profile_bin_m", "m"),
        ("--ascent-threshold-m", "ascent_threshold_m", "m"),
        ("--distance-min-step-m", "distance_min_step_m", "m"),
        ("--moving-min-speed-m-s", "moving_min_speed_m_s", "m/s"),
        ("--moving-window-s", "moving_window_s", "s"),
        ("--climb-min-gradient-percent", "climb_min_gradient_percent", "%"),
    ):
        analyse_cmd.add_argument(
            option,
            dest=attr,
            type=float,
            default=getattr(defaults, attr),
            help=f"default {getattr(defaults, attr):g} {unit}",
        )

    args = parser.parse_args(argv)
    if args.command == "inspect":
        return _inspect(args.file, stream)
    if args.command == "analyse":
        try:
            params = AnalysisParams(
                profile_bin_m=args.profile_bin_m,
                ascent_threshold_m=args.ascent_threshold_m,
                distance_min_step_m=args.distance_min_step_m,
                moving_min_speed_m_s=args.moving_min_speed_m_s,
                moving_window_s=args.moving_window_s,
                climb_min_gradient_percent=args.climb_min_gradient_percent,
            )
        except ValueError as exc:
            print(f"error: {exc}", file=stream)
            return EXIT_BAD_PARAMS
        return _analyse(args.file, params, args.json, stream)
    return EXIT_OK  # pragma: no cover - argparse enforces a command


def _utf8_stdout() -> TextIO:
    # Windows pipes default to the ANSI code page, which cannot print e.g.
    # Cyrillic track names.
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    return sys.stdout


def _load(path: Path, out: TextIO) -> tuple[bytes, GpxDocument] | None:
    try:
        data = path.read_bytes()
    except OSError as exc:
        print(f"error: cannot read {path}: {exc.strerror}", file=out)
        return None
    try:
        return data, read_gpx(data)
    except GpxReadError as exc:
        print(f"error: {path.name}: {exc.code}: {exc.message}", file=out)
        return None


def _print_file_header(path: Path, data: bytes, doc: GpxDocument, out: TextIO) -> None:
    sha = hashlib.sha256(data).hexdigest()
    print(
        f"File:     {path.name}  ({len(data) / 1024:.0f} KiB, sha256 {sha[:12]})",
        file=out,
    )
    print(
        f"Creator:  {doc.creator or '-'}   GPX {doc.gpx_version or '?'}   "
        f"waypoints: {doc.waypoint_count}",
        file=out,
    )
    _print_warnings(doc.warnings, out, indent="")


# --- inspect ------------------------------------------------------------------


def _inspect(path: Path, out: TextIO) -> int:
    loaded = _load(path, out)
    if loaded is None:
        return EXIT_READ_ERROR
    data, doc = loaded
    _print_file_header(path, data, doc, out)

    for source in doc.tracks:
        t = source.track
        print(file=out)
        print(
            f"Track {source.element}[{source.index_in_element}]  "
            f'"{t.name or "(unnamed)"}"  type: {t.activity_type or "-"}',
            file=out,
        )
        print(
            f"  points: {t.point_count}   source segments: {t.source_segment_count}",
            file=out,
        )
        print(
            f"  kind: {t.kind} ({t.kind_inferred_by})   elevation source: "
            f"{t.elevation_source} ({t.elevation_source_inferred_by})",
            file=out,
        )
        print(
            f"  elevation: {t.elevation_coverage:.0%} of points   "
            f"timestamps: {t.time_coverage:.0%} of points",
            file=out,
        )
        _print_warnings(source.read_warnings + check_track(t), out, indent="  ")

    return EXIT_OK


# --- analyse ------------------------------------------------------------------


def _analyse(path: Path, params: AnalysisParams, as_json: bool, out: TextIO) -> int:
    loaded = _load(path, out)
    if loaded is None:
        return EXIT_READ_ERROR
    data, doc = loaded
    results = [(source, analyse(source.track, params)) for source in doc.tracks]

    if as_json:
        out.write(to_json(file_report(path.name, data, doc, results)))
        return EXIT_OK

    _print_file_header(path, data, doc, out)
    for source, analysis in results:
        t = source.track
        print(file=out)
        print(
            f"Track {source.element}[{source.index_in_element}]  "
            f'"{t.name or "(unnamed)"}"  {t.kind} ({t.kind_inferred_by}), '
            f"elevation {t.elevation_source} ({t.elevation_source_inferred_by})",
            file=out,
        )
        print(
            f"  algorithm {analysis.algorithm_version}, "
            f"params {analysis.params_hash[:12]}",
            file=out,
        )
        _print_summary(analysis, out)
        _print_distribution(analysis, out)
        _print_climbs(analysis, out)
        _print_warnings(source.read_warnings + analysis.warnings, out, indent="  ")
    return EXIT_OK


def _km(metres: float | None) -> str:
    return "unknown" if metres is None else f"{metres / 1000:.2f} km"


def _m(metres: float | None) -> str:
    return "unknown" if metres is None else f"{metres:.0f} m"


def _hm(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    minutes = round(seconds / 60)
    return f"{minutes // 60}:{minutes % 60:02d} h"


def _print_summary(a: Analysis, out: TextIO) -> None:
    s = a.summary
    p = a.params
    rows = [
        (
            "Distance",
            f"{_km(s.distance_m)}   (3D {_km(s.distance_3d_m)}; unfiltered "
            f"{_km(s.distance_raw_m)}; steps < {p.distance_min_step_m:g} m ignored)",
        ),
        (
            "Ascent / descent",
            f"{_m(s.ascent_m)} / {_m(s.descent_m)}   "
            f"(hysteresis {a.params.ascent_threshold_m:g} m)",
        ),
        ("Elevation range", f"{_m(s.min_elevation_m)} .. {_m(s.max_elevation_m)}"),
        ("Elapsed time", _hm(s.elapsed_time_s)),
        (
            "Moving time",
            _hm(s.moving_time_s)
            if s.moving_time_s is None
            else f"{_hm(s.moving_time_s)}   (speed > {p.moving_min_speed_m_s:g} m/s "
            f"over {p.moving_window_s:g} s)",
        ),
    ]
    if s.moving_time_s is not None:
        rows += [
            (
                "Stopped time",
                f"{_hm(s.stopped_time_s)}   ({s.stop_count} stops >= "
                f"{p.stop_min_duration_s:g} s)",
            ),
            ("Recording gaps", _hm(s.recording_gap_s)),
            (
                "While stopped",
                f"{_km(s.distance_while_stopped_m)}   (included in distance)",
            ),
        ]
    print(file=out)
    for label, value in rows:
        print(f"  {label:<18}{value}", file=out)


def _print_distribution(a: Analysis, out: TextIO) -> None:
    total = sum(a.gradient_distribution.values())
    if total <= 0:
        return
    print(file=out)
    print(f"  Gradient distribution ({a.params.profile_bin_m:g} m bins)", file=out)
    labels = [label for label, _, _ in GRADIENT_BANDS] + [UNKNOWN_BAND]
    for label in labels:
        metres = a.gradient_distribution[label]
        if label == UNKNOWN_BAND and metres == 0:
            continue
        share = metres / total
        bar = "#" * round(share * 40)
        name = label if label == UNKNOWN_BAND else f"{label} %"
        print(f"    {name:>11}  {metres / 1000:6.2f} km  {share:4.0%}  {bar}", file=out)


def _print_climbs(a: Analysis, out: TextIO) -> None:
    print(file=out)
    p = a.params
    print(
        f"  Climb sections (>= {p.climb_min_gradient_percent:g} %, "
        f">= {p.climb_min_elevation_change_m:g} m)",
        file=out,
    )
    if not a.climbs:
        print("    none", file=out)
    for c in a.climbs:
        sign = "+" if c.direction == "up" else "-"
        print(
            f"    {c.direction:<4} {c.start_distance_m / 1000:6.2f} .. "
            f"{c.end_distance_m / 1000:6.2f} km  {sign}{c.elevation_change_m:.0f} m  "
            f"mean {c.mean_gradient_percent:+.0f} %  "
            f"steepest bin {c.max_bin_gradient_percent:+.0f} %",
            file=out,
        )


def _print_warnings(
    warnings: Sequence[QualityWarning], out: TextIO, indent: str
) -> None:
    if not warnings:
        return
    print(file=out)
    print(f"{indent}Warnings", file=out)
    for w in warnings:
        print(f"{indent}  {w.code}: {w.message}", file=out)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
