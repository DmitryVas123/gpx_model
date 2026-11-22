"""Command-line entry point. No calculations here; only reading and printing."""

import argparse
import hashlib
import io
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from alpha.domain.quality import QualityWarning, check_track
from alpha.gpx.reader import GpxReadError, read_gpx

EXIT_OK = 0
EXIT_READ_ERROR = 2


def main(argv: Sequence[str] | None = None, out: TextIO | None = None) -> int:
    stream = out or _utf8_stdout()
    parser = argparse.ArgumentParser(prog="alpha", description="ALPHA GPX analysis")
    commands = parser.add_subparsers(dest="command", required=True)

    inspect = commands.add_parser(
        "inspect", help="list tracks, inferred kind, and data-quality warnings"
    )
    inspect.add_argument("file", type=Path)

    args = parser.parse_args(argv)
    if args.command == "inspect":
        return _inspect(args.file, stream)
    return EXIT_OK  # pragma: no cover - argparse enforces a command


def _utf8_stdout() -> TextIO:
    # Windows pipes default to the ANSI code page, which cannot print e.g.
    # Cyrillic track names.
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    return sys.stdout


def _inspect(path: Path, out: TextIO) -> int:
    try:
        data = path.read_bytes()
    except OSError as exc:
        print(f"error: cannot read {path}: {exc.strerror}", file=out)
        return EXIT_READ_ERROR

    try:
        doc = read_gpx(data)
    except GpxReadError as exc:
        print(f"error: {path.name}: {exc.code}: {exc.message}", file=out)
        return EXIT_READ_ERROR

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


def _print_warnings(
    warnings: Sequence[QualityWarning], out: TextIO, indent: str
) -> None:
    if not warnings:
        return
    print(f"{indent}warnings:", file=out)
    for w in warnings:
        print(f"{indent}  {w.code}: {w.message}", file=out)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
