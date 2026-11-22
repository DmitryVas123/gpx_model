"""Plain-text rendering for the CLI. Formatting only: no calculations."""

import hashlib
from collections.abc import Sequence
from typing import TextIO

from alpha.application.scenarios import ScenarioView
from alpha.application.tracks import TrackComparison, TrackListItem
from alpha.domain.analysis import Analysis
from alpha.domain.climbs import ClimbSection
from alpha.domain.comparison import MetricDelta
from alpha.domain.profile import GRADIENT_BANDS, UNKNOWN_BAND
from alpha.domain.quality import QualityWarning, check_track
from alpha.domain.scenarios import SCENARIO_METHODS
from alpha.domain.track import Inference, Track
from alpha.gpx.reader import GpxDocument, SourceTrack
from alpha.persistence import TrackRecord

UNKNOWN = "unknown"


# --- value formatting --------------------------------------------------------------


def km(metres: float | None) -> str:
    return UNKNOWN if metres is None else f"{metres / 1000:.2f} km"


def m(metres: float | None) -> str:
    return UNKNOWN if metres is None else f"{metres:.0f} m"


def hm(seconds: float | None) -> str:
    if seconds is None:
        return UNKNOWN
    minutes = round(seconds / 60)
    return f"{minutes // 60}:{minutes % 60:02d} h"


def _signed(value: float | None, fmt: str) -> str:
    if value is None:
        return "-"
    sign = "+" if value >= 0 else "-"
    magnitude = abs(value)
    if fmt == "km":
        return f"{sign}{magnitude / 1000:.2f} km"
    if fmt == "h":
        return f"{sign}{hm(magnitude)}"
    return f"{sign}{magnitude:.0f} m"


def _guess(inferred_by: Inference) -> str:
    return "?" if inferred_by is Inference.HEURISTIC else ""


# --- files and tracks --------------------------------------------------------------


def file_header(file_name: str, data: bytes, doc: GpxDocument, out: TextIO) -> None:
    sha = hashlib.sha256(data).hexdigest()
    print(
        f"File:     {file_name}  ({len(data) / 1024:.0f} KiB, sha256 {sha[:12]})",
        file=out,
    )
    print(
        f"Creator:  {doc.creator or '-'}   GPX {doc.gpx_version or '?'}   "
        f"waypoints: {doc.waypoint_count}",
        file=out,
    )
    warnings(doc.warnings, out, indent="")


def inspected_track(source: SourceTrack, out: TextIO) -> None:
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
    warnings(source.read_warnings + check_track(t), out, indent="  ")


def track_title(title: str, track: Track, out: TextIO) -> None:
    print(file=out)
    print(
        f'{title}  "{track.name or "(unnamed)"}"  {track.kind} '
        f"({track.kind_inferred_by}), elevation {track.elevation_source} "
        f"({track.elevation_source_inferred_by})",
        file=out,
    )


def analysis(a: Analysis, read_warnings: Sequence[QualityWarning], out: TextIO) -> None:
    print(f"  algorithm {a.algorithm_version}, params {a.params_hash[:12]}", file=out)
    _summary(a, out)
    _distribution(a, out)
    _climbs(a, out)
    warnings(tuple(read_warnings) + a.warnings, out, indent="  ")


def _summary(a: Analysis, out: TextIO) -> None:
    s = a.summary
    p = a.params
    rows = [
        (
            "Distance",
            f"{km(s.distance_m)}   (3D {km(s.distance_3d_m)}; unfiltered "
            f"{km(s.distance_raw_m)}; steps < {p.distance_min_step_m:g} m ignored)",
        ),
        (
            "Ascent / descent",
            f"{m(s.ascent_m)} / {m(s.descent_m)}   "
            f"(hysteresis {p.ascent_threshold_m:g} m)",
        ),
        ("Elevation range", f"{m(s.min_elevation_m)} .. {m(s.max_elevation_m)}"),
        ("Elapsed time", hm(s.elapsed_time_s)),
        (
            "Moving time",
            hm(s.moving_time_s)
            if s.moving_time_s is None
            else f"{hm(s.moving_time_s)}   (speed > {p.moving_min_speed_m_s:g} m/s "
            f"over {p.moving_window_s:g} s)",
        ),
    ]
    if s.moving_time_s is not None:
        rows += [
            (
                "Stopped time",
                f"{hm(s.stopped_time_s)}   ({s.stop_count} stops >= "
                f"{p.stop_min_duration_s:g} s)",
            ),
            ("Recording gaps", hm(s.recording_gap_s)),
            (
                "While stopped",
                f"{km(s.distance_while_stopped_m)}   (included in distance)",
            ),
        ]
    print(file=out)
    for label, value in rows:
        print(f"  {label:<18}{value}", file=out)


def _band_labels() -> list[str]:
    return [label for label, _, _ in GRADIENT_BANDS] + [UNKNOWN_BAND]


def _band_name(label: str) -> str:
    return label if label == UNKNOWN_BAND else f"{label} %"


def _distribution(a: Analysis, out: TextIO) -> None:
    total = sum(a.gradient_distribution.values())
    if total <= 0:
        return
    print(file=out)
    print(f"  Gradient distribution ({a.params.profile_bin_m:g} m bins)", file=out)
    for label in _band_labels():
        metres = a.gradient_distribution[label]
        if label == UNKNOWN_BAND and metres == 0:
            continue
        share = metres / total
        bar = "#" * round(share * 40)
        print(
            f"    {_band_name(label):>11}  {metres / 1000:6.2f} km  "
            f"{share:4.0%}  {bar}",
            file=out,
        )


def _climb_text(c: ClimbSection) -> str:
    sign = "+" if c.direction == "up" else "-"
    return (
        f"{sign}{c.elevation_change_m:.0f} m over {c.length_m / 1000:.2f} km "
        f"from km {c.start_distance_m / 1000:.2f}, "
        f"mean {c.mean_gradient_percent:+.0f} %, "
        f"steepest bin {c.max_bin_gradient_percent:+.0f} %"
    )


def _climbs(a: Analysis, out: TextIO) -> None:
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
        print(f"    {c.direction:<4} {_climb_text(c)}", file=out)


def warnings(items: Sequence[QualityWarning], out: TextIO, indent: str) -> None:
    if not items:
        return
    print(file=out)
    print(f"{indent}Warnings", file=out)
    for w in items:
        print(f"{indent}  {w.code}: {w.message}", file=out)


# --- stored tracks -----------------------------------------------------------------


def record_line(r: TrackRecord) -> str:
    return (
        f"#{r.id} {r.kind}{_guess(r.kind_inferred_by)}  "
        f'"{r.name or "(unnamed)"}"  from {r.source_filename}'
    )


def track_list(items: Sequence[TrackListItem], out: TextIO) -> None:
    if not items:
        print("No tracks imported yet. Use: alpha import FILE.gpx", file=out)
        return
    print(
        f"{'ID':>4}  {'KIND':<10}{'DATE (UTC)':<12}{'DISTANCE':>10}{'ASCENT':>9}"
        f"{'MOVING':>9}  NAME",
        file=out,
    )
    guessed = False
    for item in items:
        r, s = item.record, item.summary
        guessed |= r.kind_inferred_by is Inference.HEURISTIC
        date = r.started_at_utc.strftime("%Y-%m-%d") if r.started_at_utc else "-"
        moving = hm(s.moving_time_s) if s.moving_time_s is not None else "-"
        link = f"  (plan #{r.planned_track_id})" if r.planned_track_id else ""
        print(
            f"{r.id:>4}  {str(r.kind) + _guess(r.kind_inferred_by):<10}{date:<12}"
            f"{km(s.distance_m):>10}{m(s.ascent_m):>9}{moving:>9}  "
            f"{r.name or '(unnamed)'}{link}",
            file=out,
        )
    if guessed:
        print(
            "\n? = kind guessed from the data; confirm with: alpha edit ID --kind ...",
            file=out,
        )


# --- comparison ---------------------------------------------------------------------

_METRIC_LABELS: dict[str, tuple[str, str]] = {
    "distance_m": ("Distance", "km"),
    "distance_3d_m": ("Distance 3D", "km"),
    "ascent_m": ("Ascent", "m"),
    "descent_m": ("Descent", "m"),
    "min_elevation_m": ("Lowest point", "m"),
    "max_elevation_m": ("Highest point", "m"),
    "elapsed_time_s": ("Elapsed time", "h"),
    "moving_time_s": ("Moving time", "h"),
}


def _value(value: float | None, fmt: str) -> str:
    return {"km": km, "m": m, "h": hm}[fmt](value)


def _metric_row(d: MetricDelta) -> str:
    label, fmt = _METRIC_LABELS[d.metric]
    ratio = f"{d.ratio:.2f}" if d.ratio is not None else "-"
    flag = "  *" if d.material else ""
    return (
        f"  {label:<15}{_value(d.a, fmt):>11}{_value(d.b, fmt):>11}"
        f"{_signed(d.delta, fmt):>12}{ratio:>8}{flag}"
    )


def comparison(c: TrackComparison, out: TextIO) -> None:
    r = c.result
    print(f"A = {record_line(c.a)}", file=out)
    print(f"B = {record_line(c.b)}", file=out)
    print(f"algorithm {r.algorithm_version}, params {r.params_hash[:12]}", file=out)
    print(file=out)
    print(f"  {'Metric':<15}{'A':>11}{'B':>11}{'B - A':>12}{'B / A':>8}", file=out)
    for d in r.metrics:
        print(_metric_row(d), file=out)
    print("  * large difference (a display hint, not a verdict)", file=out)

    print(file=out)
    print(f"  {'Gradient band':<15}{'A':>11}{'B':>11}{'B - A':>12}", file=out)
    for band in r.bands:
        if band.band == UNKNOWN_BAND and band.a_m == 0 and band.b_m == 0:
            continue
        flag = "  *" if band.material else ""
        print(
            f"  {_band_name(band.band):<15}{km(band.a_m):>11}{km(band.b_m):>11}"
            f"{_signed(band.delta_m, 'km'):>12}{flag}",
            file=out,
        )

    print(file=out)
    for title, a_climb, b_climb in (
        ("Biggest climb", r.biggest_climb_a, r.biggest_climb_b),
        ("Steepest climb", r.steepest_climb_a, r.steepest_climb_b),
    ):
        print(f"  {title}", file=out)
        print(f"    A  {_climb_text(a_climb) if a_climb else 'none'}", file=out)
        print(f"    B  {_climb_text(b_climb) if b_climb else 'none'}", file=out)

    print(file=out)
    print("  Notes", file=out)
    for note in r.notes:
        print(f"    - {note}", file=out)


# --- scenarios ----------------------------------------------------------------------


def scenarios(v: ScenarioView, out: TextIO) -> None:
    r = v.result
    a = r.assumptions
    spec = SCENARIO_METHODS[r.method]
    print(record_line(v.record), file=out)
    print(
        f"method {r.method} v{r.method_version} ({spec.description}); "
        f"analysis {r.algorithm_version}, params {r.params_hash[:12]}",
        file=out,
    )
    print("All durations below are ESTIMATES from the stated assumptions.", file=out)

    source = f"{a.source}: {a.label}" if a.label else str(a.source)
    print(file=out)
    print(f"  Assumptions ({source})", file=out)
    print(
        f"    horizontal {a.horizontal_speed_m_h / 1000:g} km/h, "
        f"ascent {a.ascent_rate_m_h:g} m/h, descent {a.descent_rate_m_h:g} m/h, "
        f"breaks {hm(a.break_s)}, delay {hm(a.delay_s)}",
        file=out,
    )
    print("  Inputs (calculated from the track)", file=out)
    print(
        f"    distance {km(r.inputs.distance_m)}, ascent {m(r.inputs.ascent_m)}, "
        f"descent {m(r.inputs.descent_m)}",
        file=out,
    )

    print(file=out)
    print(
        f"  {'Scenario':<10}{'speed':>7}{'horizontal':>12}{'vertical':>10}"
        f"{'moving':>10}{'breaks+delay':>14}{'total':>10}",
        file=out,
    )
    for s in r.scenarios:
        b = s.breakdown
        print(
            f"  {s.label:<10}{'x' + format(s.speed_multiplier, 'g'):>7}"
            f"{hm(b.horizontal_s):>12}{hm(b.vertical_s):>10}{hm(b.moving_s):>10}"
            f"{hm(b.break_s + b.delay_s):>14}{hm(s.estimated_duration_s):>10}",
            file=out,
        )

    if v.observed:
        print(file=out)
        print("  Observed (recorded activity times, not estimates)", file=out)
        for o in v.observed:
            print(
                f'    #{o.track_id} "{o.name or "(unnamed)"}" ({o.relation}): '
                f"moving {hm(o.moving_time_s)}, elapsed {hm(o.elapsed_time_s)}",
                file=out,
            )
            if o.implied_speed_multiplier is not None:
                print(
                    f"      on its own distance and ascent, the method matches this "
                    f"moving time at x{o.implied_speed_multiplier:.2f} speed "
                    "(one activity: a reference point, not a personal model)",
                    file=out,
                )

    print(file=out)
    print("  Notes", file=out)
    for note in r.uncertainty_notes:
        print(f"    - {note}", file=out)
