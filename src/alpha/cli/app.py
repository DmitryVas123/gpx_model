"""Command-line entry point. Parses arguments, calls use cases, renders output."""

import argparse
import io
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TextIO

from alpha.application.tracks import (
    InvalidEditError,
    TrackNotFoundError,
    Workspace,
    compare_tracks,
    delete_source_of_track,
    edit_track,
    import_file,
    list_tracks,
    show_track,
)
from alpha.cli import render
from alpha.domain.analysis import AnalysisParams, analyse
from alpha.domain.track import ElevationSource, TrackKind
from alpha.gpx.reader import GpxDocument, GpxReadError, read_gpx
from alpha.persistence import SchemaMismatchError
from alpha.report import (
    comparison_report,
    file_report,
    stored_track_ref,
    to_json,
    track_report,
)

EXIT_OK = 0
EXIT_READ_ERROR = 2
EXIT_BAD_PARAMS = 3
EXIT_NOT_FOUND = 4
EXIT_INVALID = 5

DATA_DIR_ENV = "ALPHA_DATA_DIR"
DEFAULT_DATA_DIR = Path("data") / "local"

# (option, AnalysisParams field, unit) exposed on analysing commands.
PARAM_OPTIONS = (
    ("--profile-bin-m", "profile_bin_m", "m"),
    ("--ascent-threshold-m", "ascent_threshold_m", "m"),
    ("--distance-min-step-m", "distance_min_step_m", "m"),
    ("--moving-min-speed-m-s", "moving_min_speed_m_s", "m/s"),
    ("--moving-window-s", "moving_window_s", "s"),
    ("--climb-min-gradient-percent", "climb_min_gradient_percent", "%"),
)


def main(argv: Sequence[str] | None = None, out: TextIO | None = None) -> int:
    stream = out or _utf8_stdout()
    args = _parser().parse_args(argv)
    handler: Callable[[argparse.Namespace, TextIO], int] = args.handler
    try:
        return handler(args, stream)
    except SchemaMismatchError as exc:
        print(f"error: {exc}", file=stream)
        return EXIT_INVALID


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="alpha", description="ALPHA GPX analysis")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.environ.get(DATA_DIR_ENV, DEFAULT_DATA_DIR)),
        help=f"database and stored GPX files (default: ${DATA_DIR_ENV} or "
        f"{DEFAULT_DATA_DIR.as_posix()})",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    def command(
        name: str, help_text: str, handler: Callable[[argparse.Namespace, TextIO], int]
    ) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, help=help_text, description=help_text)
        sub.set_defaults(handler=handler)
        return sub

    sub = command(
        "inspect", "read a GPX file and report tracks and data quality", _inspect
    )
    sub.add_argument("file", type=Path)

    sub = command("analyse", "analyse a GPX file without storing it", _analyse_file)
    sub.add_argument("file", type=Path)
    _add_analysis_options(sub)

    sub = command("import", "store GPX files and analyse them", _import)
    sub.add_argument("files", type=Path, nargs="+")

    command("list", "list stored tracks", _list)

    sub = command("show", "analysis of a stored track", _show)
    sub.add_argument("track_id", type=int)
    _add_analysis_options(sub)

    sub = command("compare", "compare stored track B against track A", _compare)
    sub.add_argument("track_a", type=int)
    sub.add_argument("track_b", type=int)
    _add_analysis_options(sub)

    sub = command("edit", "correct kind or elevation source, or link to a plan", _edit)
    sub.add_argument("track_id", type=int)
    sub.add_argument("--kind", choices=[k.value for k in TrackKind])
    sub.add_argument("--elevation-source", choices=[e.value for e in ElevationSource])
    link = sub.add_mutually_exclusive_group()
    link.add_argument(
        "--planned-track", type=int, metavar="ID", help="link to the planned route ID"
    )
    link.add_argument("--no-planned-track", action="store_true", help="remove the link")

    sub = command(
        "delete", "delete a stored GPX file and all its tracks (needs --yes)", _delete
    )
    sub.add_argument("track_id", type=int)
    sub.add_argument("--yes", action="store_true", help="really delete")
    return parser


def _add_analysis_options(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("--json", action="store_true", help="print a JSON report")
    defaults = AnalysisParams()
    for option, attr, unit in PARAM_OPTIONS:
        sub.add_argument(
            option,
            dest=attr,
            type=float,
            default=getattr(defaults, attr),
            help=f"default {getattr(defaults, attr):g} {unit}",
        )


def _params(args: argparse.Namespace) -> AnalysisParams:
    return AnalysisParams(**{attr: getattr(args, attr) for _, attr, _ in PARAM_OPTIONS})


def _utf8_stdout() -> TextIO:
    # Windows pipes default to the ANSI code page, which cannot print e.g.
    # Cyrillic track names.
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    return sys.stdout


def _read(path: Path, out: TextIO) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError as exc:
        print(f"error: cannot read {path}: {exc.strerror}", file=out)
        return None


def _load(path: Path, out: TextIO) -> tuple[bytes, GpxDocument] | None:
    data = _read(path, out)
    if data is None:
        return None
    try:
        return data, read_gpx(data)
    except GpxReadError as exc:
        print(f"error: {path.name}: {exc.code}: {exc.message}", file=out)
        return None


# --- file commands (nothing stored) -----------------------------------------------


def _inspect(args: argparse.Namespace, out: TextIO) -> int:
    loaded = _load(args.file, out)
    if loaded is None:
        return EXIT_READ_ERROR
    data, doc = loaded
    render.file_header(args.file.name, data, doc, out)
    for source in doc.tracks:
        render.inspected_track(source, out)
    return EXIT_OK


def _analyse_file(args: argparse.Namespace, out: TextIO) -> int:
    try:
        params = _params(args)
    except ValueError as exc:
        print(f"error: {exc}", file=out)
        return EXIT_BAD_PARAMS
    loaded = _load(args.file, out)
    if loaded is None:
        return EXIT_READ_ERROR
    data, doc = loaded
    results = [(source, analyse(source.track, params)) for source in doc.tracks]
    if args.json:
        out.write(to_json(file_report(args.file.name, data, doc, results)))
        return EXIT_OK
    render.file_header(args.file.name, data, doc, out)
    for source, a in results:
        render.track_title(
            f"Track {source.element}[{source.index_in_element}]", source.track, out
        )
        render.analysis(a, source.read_warnings, out)
    return EXIT_OK


# --- stored-track commands -------------------------------------------------------


def _with_workspace(
    args: argparse.Namespace, out: TextIO, action: Callable[[Workspace], int]
) -> int:
    ws = Workspace(args.data_dir)
    try:
        return action(ws)
    except TrackNotFoundError as exc:
        print(f"error: {exc}", file=out)
        return EXIT_NOT_FOUND
    except InvalidEditError as exc:
        print(f"error: {exc}", file=out)
        return EXIT_INVALID
    finally:
        ws.close()


def _import(args: argparse.Namespace, out: TextIO) -> int:
    def action(ws: Workspace) -> int:
        status = EXIT_OK
        for path in args.files:
            data = _read(path, out)
            if data is None:
                status = EXIT_READ_ERROR
                continue
            try:
                outcome = import_file(ws, path.name, data)
            except GpxReadError as exc:
                print(f"error: {path.name}: {exc.code}: {exc.message}", file=out)
                status = EXIT_READ_ERROR
                continue
            verb = "imported" if outcome.created else "already imported"
            print(f"{path.name}: {verb}", file=out)
            for record in outcome.tracks:
                print(f"  {render.record_line(record)}", file=out)
        print(f"data directory: {args.data_dir}", file=out)
        return status

    return _with_workspace(args, out, action)


def _list(args: argparse.Namespace, out: TextIO) -> int:
    def action(ws: Workspace) -> int:
        render.track_list(list_tracks(ws), out)
        return EXIT_OK

    return _with_workspace(args, out, action)


def _show(args: argparse.Namespace, out: TextIO) -> int:
    try:
        params = _params(args)
    except ValueError as exc:
        print(f"error: {exc}", file=out)
        return EXIT_BAD_PARAMS

    def action(ws: Workspace) -> int:
        view = show_track(ws, args.track_id, params)
        r = view.record
        if args.json:
            report = {
                "stored": stored_track_ref(r.id, r.source_filename, r.planned_track_id),
                **track_report(view.track, view.analysis),
            }
            out.write(to_json(report))
            return EXIT_OK
        render.track_title(f"#{r.id}", view.track, out)
        print(f"  from {r.source_filename}", end="", file=out)
        if r.planned_track_id:
            print(f", follows plan #{r.planned_track_id}", end="", file=out)
        print(file=out)
        render.analysis(view.analysis, r.read_warnings, out)
        return EXIT_OK

    return _with_workspace(args, out, action)


def _compare(args: argparse.Namespace, out: TextIO) -> int:
    try:
        params = _params(args)
    except ValueError as exc:
        print(f"error: {exc}", file=out)
        return EXIT_BAD_PARAMS

    def action(ws: Workspace) -> int:
        c = compare_tracks(ws, args.track_a, args.track_b, params)
        if args.json:
            out.write(
                to_json(
                    comparison_report(
                        stored_track_ref(
                            c.a.id, c.a.source_filename, c.a.planned_track_id
                        ),
                        stored_track_ref(
                            c.b.id, c.b.source_filename, c.b.planned_track_id
                        ),
                        c.result,
                    )
                )
            )
            return EXIT_OK
        render.comparison(c, out)
        return EXIT_OK

    return _with_workspace(args, out, action)


def _edit(args: argparse.Namespace, out: TextIO) -> int:
    if not (
        args.kind
        or args.elevation_source
        or args.planned_track
        or args.no_planned_track
    ):
        print("error: nothing to change; see alpha edit --help", file=out)
        return EXIT_BAD_PARAMS

    def action(ws: Workspace) -> int:
        record = edit_track(
            ws,
            args.track_id,
            kind=TrackKind(args.kind) if args.kind else None,
            elevation_source=(
                ElevationSource(args.elevation_source)
                if args.elevation_source
                else None
            ),
            planned_track_id=args.planned_track,
            clear_planned_track=args.no_planned_track,
        )
        link = (
            f", follows plan #{record.planned_track_id}"
            if record.planned_track_id
            else ""
        )
        print(
            f"{render.record_line(record)}\n  elevation source "
            f"{record.elevation_source} ({record.elevation_source_inferred_by}){link}",
            file=out,
        )
        return EXIT_OK

    return _with_workspace(args, out, action)


def _delete(args: argparse.Namespace, out: TextIO) -> int:
    def action(ws: Workspace) -> int:
        plan = delete_source_of_track(ws, args.track_id, confirm=args.yes)
        verb = "Deleted" if plan.deleted else "Would delete"
        print(
            f"{verb} {plan.source_filename} with {len(plan.tracks)} track(s):", file=out
        )
        for record in plan.tracks:
            print(f"  {render.record_line(record)}", file=out)
        if not plan.deleted:
            print("Nothing deleted. Repeat with --yes to delete.", file=out)
        return EXIT_OK

    return _with_workspace(args, out, action)
