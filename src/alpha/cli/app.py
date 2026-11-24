"""Command-line entry point. Parses arguments, calls use cases, renders output."""

import argparse
import io
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, TextIO

from alpha.application.baseline import BaselineNotFittedError, personal_baseline
from alpha.application.elevation import (
    elevation_check,
    enrich_all,
    enrich_track,
    get_tiles,
    plan_tiles,
)
from alpha.application.scenarios import estimate_duration, estimate_personal
from alpha.application.tracks import (
    DemProfileMissingError,
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
from alpha.cli.config import opentopography_api_key
from alpha.dem import (
    DATASETS,
    DEFAULT_DATASET,
    DemDownloadError,
    DemUnavailableError,
    MetalinkError,
    open_dem,
)
from alpha.domain.analysis import AnalysisParams, analyse
from alpha.domain.scenarios import (
    DEFAULT_SPEED_MULTIPLIERS,
    DIN_33466_STANDARD,
    DinAssumptions,
    ScenarioInputError,
    din_assumptions,
)
from alpha.domain.track import Effort, ElevationSource, TrackKind
from alpha.explain import TOPICS, explain
from alpha.gpx.reader import GpxDocument, GpxReadError, read_gpx
from alpha.llm import DEFAULT_MODEL, OllamaRewriter
from alpha.modelling import EFFORT_SELECTIONS, BaselineParams
from alpha.persistence import SchemaMismatchError
from alpha.report import (
    baseline_report,
    comparison_report,
    elevation_check_report,
    explanation_report,
    file_report,
    scenario_report,
    stored_track_ref,
    to_json,
    track_report,
)

EXIT_OK = 0
EXIT_READ_ERROR = 2
EXIT_BAD_PARAMS = 3
EXIT_NOT_FOUND = 4
EXIT_INVALID = 5
EXIT_UNAVAILABLE = 6

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
    _add_analysis_options(sub, elevation=False)

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

    sub = command(
        "edit",
        "correct kind or elevation source, link to a plan, or note the effort",
        _edit,
    )
    sub.add_argument("track_id", type=int)
    sub.add_argument("--kind", choices=[k.value for k in TrackKind])
    sub.add_argument("--elevation-source", choices=[e.value for e in ElevationSource])
    link = sub.add_mutually_exclusive_group()
    link.add_argument(
        "--planned-track", type=int, metavar="ID", help="link to the planned route ID"
    )
    link.add_argument("--no-planned-track", action="store_true", help="remove the link")
    sub.add_argument(
        "--effort",
        choices=[*(e.value for e in Effort), "none"],
        help="how hard the activity was, in your judgement (none clears it)",
    )

    sub = command(
        "scenarios",
        "estimated duration range (DIN 33466) next to observed times",
        _scenarios,
    )
    sub.add_argument("track_id", type=int)
    preset = DIN_33466_STANDARD
    sub.add_argument(
        "--horizontal-speed-km-h",
        type=float,
        help=f"default {preset.horizontal_speed_m_h / 1000:g} (DIN 33466 standard)",
    )
    sub.add_argument(
        "--ascent-rate-m-h",
        type=float,
        help=f"default {preset.ascent_rate_m_h:g} (DIN 33466 standard)",
    )
    sub.add_argument(
        "--descent-rate-m-h",
        type=float,
        help=f"default {preset.descent_rate_m_h:g} (DIN 33466 standard)",
    )
    sub.add_argument("--break-min", type=float, default=0.0, help="default 0")
    sub.add_argument("--delay-min", type=float, default=0.0, help="default 0")
    sub.add_argument(
        "--speed-multipliers",
        help="comma-separated, applied to all speeds (default "
        + ",".join(f"{k:g}" for k in DEFAULT_SPEED_MULTIPLIERS)
        + "; with --personal: your own 95 % range)",
    )
    sub.add_argument(
        "--personal",
        action="store_true",
        help="use speeds fitted from your activities (see 'alpha baseline'); "
        "fails if none is fitted yet",
    )
    sub.add_argument(
        "--effort",
        choices=sorted(EFFORT_SELECTIONS),
        default=BaselineParams().effort,
        help="with --personal: which efforts the speeds are fitted on "
        "(default %(default)s)",
    )
    _add_analysis_options(sub)

    sub = command(
        "baseline",
        "personal speed multiplier on DIN 33466 from your activities (fitted)",
        _baseline,
    )
    sub.add_argument(
        "--min-routes",
        type=int,
        default=BaselineParams().min_groups,
        help="distinct routes needed before fitting model 1 (default %(default)s)",
    )
    sub.add_argument(
        "--min-routes-split",
        type=int,
        default=BaselineParams().split_min_groups,
        help="distinct routes needed before fitting model 2 (default %(default)s)",
    )
    sub.add_argument(
        "--effort",
        choices=sorted(EFFORT_SELECTIONS),
        default=BaselineParams().effort,
        help="which efforts to fit (default %(default)s: normal and not stated)",
    )
    _add_analysis_options(sub)

    sub = command(
        "enrich",
        "add DEM elevation (OpenTopography) and compare it with the recorded one",
        _enrich,
    )
    target = sub.add_mutually_exclusive_group(required=True)
    target.add_argument("track_id", type=int, nargs="?")
    target.add_argument(
        "--all",
        action="store_true",
        help="every track without a profile for the dataset (all with --refresh)",
    )
    sub.add_argument(
        "--dataset",
        default=DEFAULT_DATASET,
        choices=sorted(DATASETS),
        help="DEM dataset",
    )
    sub.add_argument(
        "--refresh", action="store_true", help="sample again even if already stored"
    )
    sub.add_argument(
        "--offline",
        action="store_true",
        help="never download; only use stored profiles and cached tiles",
    )
    _add_analysis_options(sub, elevation=False)

    sub = command(
        "get-tiles",
        "download local DEM tiles listed in a metalink file (asks first)",
        _get_tiles,
    )
    sub.add_argument("metalink", type=Path, help="e.g. dgm1.meta4 from the provider")
    sub.add_argument(
        "--dataset",
        default="BY_DGM1",
        choices=sorted(k for k, d in DATASETS.items() if d.download_hosts),
        help="default %(default)s",
    )
    sub.add_argument(
        "--for-tracks",
        action="store_true",
        help="only tiles under stored tracks (smaller; tells the server which "
        "1 km squares your tracks cross)",
    )
    sub.add_argument("--yes", action="store_true", help="really download")

    sub = command(
        "explain",
        "explain a result in plain language (templates; --llm: local model)",
        _explain,
    )
    sub.add_argument("topic", choices=sorted(TOPICS))
    sub.add_argument(
        "track_id", type=int, nargs="?", help="needed for scenarios and elevation"
    )
    sub.add_argument(
        "--llm",
        action="store_true",
        help="let the local Ollama model reword it (checked against the report)",
    )
    sub.add_argument("--model", default=DEFAULT_MODEL, help="default %(default)s")
    sub.add_argument("--language", default="English", help="with --llm")
    sub.add_argument("--personal", action="store_true", help="scenarios: fitted speeds")
    sub.add_argument("--break-min", type=float, default=0.0, help="scenarios")
    sub.add_argument(
        "--dataset", default="BY_DGM1", choices=sorted(DATASETS), help="elevation"
    )
    sub.add_argument(
        "--effort",
        choices=sorted(EFFORT_SELECTIONS),
        default=BaselineParams().effort,
        help="baseline and --personal",
    )
    _add_analysis_options(sub)

    sub = command("serve", "run the HTTP API and the local web page", _serve)
    sub.add_argument(
        "--host",
        default="127.0.0.1",
        help="default 127.0.0.1 (this computer only; the data is personal)",
    )
    sub.add_argument("--port", type=int, default=8000, help="default 8000")
    sub.add_argument(
        "--llm-model",
        default=DEFAULT_MODEL,
        help="local Ollama model for 'reword' on the page (default %(default)s)",
    )

    sub = command(
        "delete", "delete a stored GPX file and all its tracks (needs --yes)", _delete
    )
    sub.add_argument("track_id", type=int)
    sub.add_argument("--yes", action="store_true", help="really delete")
    return parser


def _add_analysis_options(
    sub: argparse.ArgumentParser, *, elevation: bool = True
) -> None:
    sub.add_argument("--json", action="store_true", help="print a JSON report")
    defaults = AnalysisParams()
    if elevation:
        sub.add_argument(
            "--elevation",
            default=defaults.elevation,
            help="'recorded' (default) or 'dem:<dataset>' after 'alpha enrich', "
            "e.g. dem:COP30",
        )
    for option, attr, unit in PARAM_OPTIONS:
        sub.add_argument(
            option,
            dest=attr,
            type=float,
            default=getattr(defaults, attr),
            help=f"default {getattr(defaults, attr):g} {unit}",
        )


def _params(args: argparse.Namespace) -> AnalysisParams:
    values = {attr: getattr(args, attr) for _, attr, _ in PARAM_OPTIONS}
    return AnalysisParams(
        **values, elevation=getattr(args, "elevation", AnalysisParams().elevation)
    )


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
    except DemProfileMissingError as exc:
        print(f"error: {exc}", file=out)
        return EXIT_UNAVAILABLE
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
                **track_report(view.track, view.analysis, view.dem),
            }
            out.write(to_json(report))
            return EXIT_OK
        render.track_title(f"#{r.id}", view.track, out)
        print(f"  from {r.source_filename}", end="", file=out)
        if r.planned_track_id:
            print(f", follows plan #{r.planned_track_id}", end="", file=out)
        print(file=out)
        render.analysis(view.analysis, r.read_warnings, out, view.dem)
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
                        c.dem_a,
                        c.dem_b,
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
        or args.effort
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
            effort=Effort(args.effort) if args.effort not in (None, "none") else None,
            clear_effort=args.effort == "none",
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


def _assumptions(args: argparse.Namespace) -> DinAssumptions:
    return din_assumptions(
        horizontal_speed_m_h=(
            None
            if args.horizontal_speed_km_h is None
            else args.horizontal_speed_km_h * 1000
        ),
        ascent_rate_m_h=args.ascent_rate_m_h,
        descent_rate_m_h=args.descent_rate_m_h,
        break_s=args.break_min * 60,
        delay_s=args.delay_min * 60,
    )


def _multipliers(text: str) -> tuple[float, ...]:
    try:
        values = tuple(float(part) for part in text.split(",") if part.strip())
    except ValueError as exc:
        raise ValueError(f"invalid --speed-multipliers {text!r}") from exc
    if not values or any(v <= 0 for v in values):
        raise ValueError("--speed-multipliers must be positive numbers")
    return values


def _scenarios(args: argparse.Namespace, out: TextIO) -> int:
    speeds_given = any(
        v is not None
        for v in (
            args.horizontal_speed_km_h,
            args.ascent_rate_m_h,
            args.descent_rate_m_h,
        )
    )
    if args.personal and speeds_given:
        print("error: --personal uses fitted speeds; omit the speed options", file=out)
        return EXIT_BAD_PARAMS
    try:
        params = _params(args)
        assumptions = _assumptions(args)
        multipliers = (
            _multipliers(args.speed_multipliers)
            if args.speed_multipliers is not None
            else None
        )
        baseline_params = BaselineParams(effort=args.effort)
    except ValueError as exc:
        print(f"error: {exc}", file=out)
        return EXIT_BAD_PARAMS

    def action(ws: Workspace) -> int:
        try:
            if args.personal:
                view = estimate_personal(
                    ws,
                    args.track_id,
                    params,
                    baseline_params,
                    break_s=assumptions.break_s,
                    delay_s=assumptions.delay_s,
                    speed_multipliers=multipliers,
                )
            else:
                view = estimate_duration(
                    ws,
                    args.track_id,
                    assumptions,
                    multipliers or DEFAULT_SPEED_MULTIPLIERS,
                    params,
                )
        except ScenarioInputError as exc:
            print(f"error: track {args.track_id}: {exc}", file=out)
            return EXIT_INVALID
        except BaselineNotFittedError as exc:
            print(f"error: {exc}", file=out)
            return EXIT_INVALID
        if args.json:
            r = view.record
            ref = stored_track_ref(r.id, r.source_filename, r.planned_track_id)
            out.write(
                to_json(
                    scenario_report(
                        ref, view.result, view.observed, view.dem, view.personal
                    )
                )
            )
            return EXIT_OK
        render.scenarios(view, out)
        return EXIT_OK

    return _with_workspace(args, out, action)


def _enrich(args: argparse.Namespace, out: TextIO) -> int:
    if args.all and args.json:
        print("error: --json reports one track; use it without --all", file=out)
        return EXIT_BAD_PARAMS
    try:
        params = _params(args)
    except ValueError as exc:
        print(f"error: {exc}", file=out)
        return EXIT_BAD_PARAMS
    key = None if args.offline else opentopography_api_key()
    provider = open_dem(args.data_dir / "dem", args.dataset, key)

    def action(ws: Workspace) -> int:
        if args.all:
            batch = enrich_all(ws, provider, refresh=args.refresh, params=params)
            render.enrich_batch(batch, out)
            return EXIT_UNAVAILABLE if batch.failed else EXIT_OK
        try:
            view = enrich_track(
                ws, args.track_id, provider, refresh=args.refresh, params=params
            )
        except (DemUnavailableError, DemDownloadError) as exc:
            print(f"error: {exc}", file=out)
            return EXIT_UNAVAILABLE
        if args.json:
            r = view.record
            ref = stored_track_ref(r.id, r.source_filename, r.planned_track_id)
            out.write(to_json(elevation_check_report(ref, view)))
            return EXIT_OK
        render.elevation(view, out)
        return EXIT_OK

    return _with_workspace(args, out, action)


def _baseline(args: argparse.Namespace, out: TextIO) -> int:
    try:
        params = _params(args)
        baseline_params = BaselineParams(
            min_groups=args.min_routes,
            split_min_groups=args.min_routes_split,
            effort=args.effort,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=out)
        return EXIT_BAD_PARAMS

    def action(ws: Workspace) -> int:
        view = personal_baseline(ws, params, baseline_params)
        if args.json:
            out.write(to_json(baseline_report(view)))
        else:
            render.baseline(view, out)
        return EXIT_OK

    return _with_workspace(args, out, action)


def _get_tiles(args: argparse.Namespace, out: TextIO) -> int:
    data = _read(args.metalink, out)
    if data is None:
        return EXIT_READ_ERROR

    def action(ws: Workspace) -> int:
        try:
            plan = plan_tiles(
                ws,
                args.data_dir / "dem",
                data,
                args.dataset,
                for_tracks=args.for_tracks,
            )
        except MetalinkError as exc:
            print(f"error: {exc}", file=out)
            return EXIT_INVALID
        render.tile_plan(plan, out)
        if plan.to_download == 0:
            return EXIT_OK
        if not args.yes:
            print("Nothing downloaded yet. Repeat with --yes to download.", file=out)
            return EXIT_OK

        def progress(i: int, n: int, name: str) -> None:
            print(f"  [{i}/{n}] {name}", file=out, flush=True)

        try:
            result = get_tiles(plan, progress=progress)
        except (DemDownloadError, MetalinkError) as exc:
            print(f"error: {exc}", file=out)
            return EXIT_UNAVAILABLE
        print(
            f"Downloaded {len(result.downloaded)} tile(s) to {plan.directory}. "
            f"Next: alpha enrich --all --dataset {plan.dataset.id}",
            file=out,
        )
        return EXIT_OK

    return _with_workspace(args, out, action)


def _explain(args: argparse.Namespace, out: TextIO) -> int:
    if args.topic in ("scenarios", "elevation") and args.track_id is None:
        print(f"error: 'explain {args.topic}' needs a track ID", file=out)
        return EXIT_BAD_PARAMS
    try:
        params = _params(args)
        baseline_params = BaselineParams(effort=args.effort)
    except ValueError as exc:
        print(f"error: {exc}", file=out)
        return EXIT_BAD_PARAMS

    def action(ws: Workspace) -> int:
        try:
            report = _report_for(args, ws, params, baseline_params)
        except (ScenarioInputError, BaselineNotFittedError) as exc:
            print(f"error: {exc}", file=out)
            return EXIT_INVALID
        rewriter = OllamaRewriter(args.model) if args.llm else None
        explanation = explain(
            args.topic, report, rewriter, model=args.model, language=args.language
        )
        notes = rewriter.notes if rewriter is not None else []
        if args.json:
            out.write(to_json(explanation_report(explanation, notes)))
        else:
            render.explanation(explanation, notes, out)
        return EXIT_OK

    return _with_workspace(args, out, action)


def _report_for(
    args: argparse.Namespace,
    ws: Workspace,
    params: AnalysisParams,
    baseline_params: BaselineParams,
) -> dict[str, Any]:
    """The same JSON report the matching command prints with --json."""
    if args.topic == "baseline":
        return baseline_report(personal_baseline(ws, params, baseline_params))
    if args.topic == "elevation":
        view = elevation_check(ws, args.track_id, args.dataset, params)
        r = view.record
        return elevation_check_report(
            stored_track_ref(r.id, r.source_filename, r.planned_track_id), view
        )
    if args.personal:
        sv = estimate_personal(
            ws, args.track_id, params, baseline_params, break_s=args.break_min * 60
        )
    else:
        sv = estimate_duration(
            ws,
            args.track_id,
            din_assumptions(break_s=args.break_min * 60),
            DEFAULT_SPEED_MULTIPLIERS,
            params,
        )
    r = sv.record
    return scenario_report(
        stored_track_ref(r.id, r.source_filename, r.planned_track_id),
        sv.result,
        sv.observed,
        sv.dem,
        sv.personal,
    )


def _serve(args: argparse.Namespace, out: TextIO) -> int:
    from alpha.api.server import run  # imported lazily: FastAPI is only needed here

    print(f"ALPHA on http://{args.host}:{args.port}  (data: {args.data_dir})", file=out)
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(
            "warning: listening beyond this computer; the API has no authentication",
            file=out,
        )
    key = opentopography_api_key()
    print(
        "DEM downloads: "
        + (
            "enabled (OpenTopography key found; only on explicit request)"
            if key
            else "disabled (no OPENTOPOGRAPHY_API_KEY; cached tiles only)"
        ),
        file=out,
    )
    run(args.data_dir, args.host, args.port, key, args.llm_model)
    return EXIT_OK


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
