"""Plain-text rendering for the CLI. Formatting only: no calculations."""

import hashlib
from collections.abc import Sequence
from typing import TextIO

from alpha.application.baseline import BaselineView
from alpha.application.elevation import ElevationView, EnrichBatch, TilePlan
from alpha.application.scenarios import ScenarioView
from alpha.application.tracks import TrackComparison, TrackListItem
from alpha.dem import DATASETS
from alpha.domain.analysis import Analysis
from alpha.domain.climbs import ClimbSection
from alpha.domain.comparison import MetricDelta
from alpha.domain.profile import GRADIENT_BANDS, UNKNOWN_BAND
from alpha.domain.quality import QualityWarning, check_track
from alpha.domain.scenarios import SCENARIO_METHODS, DinAssumptions
from alpha.domain.track import Inference, Track
from alpha.gpx.reader import GpxDocument, SourceTrack
from alpha.modelling import Validation
from alpha.persistence import DemProfileRecord, TrackRecord

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


def elevation_line(dem: DemProfileRecord | None) -> str:
    if dem is None:
        return "elevation: recorded"
    return (
        f"elevation: DEM {dem.dataset_name} ({dem.resolution_m:g} m {dem.surface} "
        f"model, {dem.vertical_datum}), sampled every {dem.sample_spacing_m:g} m"
    )


def attribution(dems: Sequence[DemProfileRecord | None], out: TextIO) -> None:
    """Print each DEM attribution once; required wherever DEM data is used."""
    for text in dict.fromkeys(d.attribution for d in dems if d is not None):
        print(f"  Attribution: {text}", file=out)


def analysis(
    a: Analysis,
    read_warnings: Sequence[QualityWarning],
    out: TextIO,
    dem: DemProfileRecord | None = None,
) -> None:
    print(
        f"  algorithm {a.algorithm_version}, params {a.params_hash[:12]}; "
        f"{elevation_line(dem)}",
        file=out,
    )
    _summary(a, out)
    _distribution(a, out)
    _climbs(a, out)
    warnings(tuple(read_warnings) + a.warnings, out, indent="  ")
    attribution([dem], out)


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
    effort = f"  [{r.effort} effort]" if r.effort else ""
    return (
        f"#{r.id} {r.kind}{_guess(r.kind_inferred_by)}  "
        f'"{r.name or "(unnamed)"}"  from {r.source_filename}{effort}'
    )


def _dem_label(item: TrackListItem) -> str:
    return ",".join(item.dem_datasets) or "-"


def track_list(items: Sequence[TrackListItem], out: TextIO) -> None:
    if not items:
        print("No tracks imported yet. Use: alpha import FILE.gpx", file=out)
        return
    dem_w = max(len(_dem_label(i)) for i in items) + 2
    print(
        f"{'ID':>4}  {'KIND':<10}{'DATE (UTC)':<12}{'DISTANCE':>10}{'ASCENT':>9}"
        f"{'MOVING':>9}  {'DEM':<{dem_w}}NAME",
        file=out,
    )
    guessed = False
    for item in items:
        r, s = item.record, item.summary
        guessed |= r.kind_inferred_by is Inference.HEURISTIC
        date = r.started_at_utc.strftime("%Y-%m-%d") if r.started_at_utc else "-"
        moving = hm(s.moving_time_s) if s.moving_time_s is not None else "-"
        link = f"  (plan #{r.planned_track_id})" if r.planned_track_id else ""
        dem = _dem_label(item)
        print(
            f"{r.id:>4}  {str(r.kind) + _guess(r.kind_inferred_by):<10}{date:<12}"
            f"{km(s.distance_m):>10}{m(s.ascent_m):>9}{moving:>9}  {dem:<{dem_w}}"
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
    print(f"A {elevation_line(c.dem_a)}", file=out)
    print(f"B {elevation_line(c.dem_b)}", file=out)
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
    attribution([c.dem_a, c.dem_b], out)


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
    print(elevation_line(v.dem), file=out)
    print("All durations below are ESTIMATES from the stated assumptions.", file=out)

    source = f"{a.source}: {a.label}" if a.label else str(a.source)
    print(file=out)
    print(f"  Assumptions ({source})", file=out)
    print(
        f"    horizontal {a.horizontal_speed_m_h / 1000:.3g} km/h, "
        f"ascent {a.ascent_rate_m_h:.0f} m/h, descent {a.descent_rate_m_h:.0f} m/h, "
        f"breaks {hm(a.break_s)}, delay {hm(a.delay_s)}",
        file=out,
    )
    if v.personal is not None:
        q = v.personal
        print(
            f"    fitted with model '{q.model}' on {q.n_activities} activities, "
            f"{q.n_routes} routes (effort '{q.effort}'); leave-one-route-out "
            f"error median {q.median_abs_error:.0%}",
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
            f"  {s.label:<10}{'x' + format(s.speed_multiplier, '.3g'):>7}"
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
    attribution([v.dem], out)


# --- DEM elevation ------------------------------------------------------------------


def _diff(value: float | None) -> str:
    return UNKNOWN if value is None else f"{value:+.1f} m"


def elevation(v: ElevationView, out: TextIO) -> None:
    p, c = v.profile, v.comparison
    print(record_line(v.record), file=out)
    print(
        f"DEM: {p.dataset_name} ({p.resolution_m:g} m {p.surface} model, heights "
        f"{p.vertical_datum}), sampled every {p.sample_spacing_m:g} m along the track",
        file=out,
    )
    local = _is_local(p.dataset_id)
    if v.reused:
        source = "stored profile (nothing downloaded)"
    elif v.downloaded_tiles:
        source = f"downloaded tiles {', '.join(v.downloaded_tiles)}"
    elif local:
        source = "local tiles (nothing downloaded)"
    else:
        source = "cached tiles (nothing downloaded)"
    print(
        f"Tiles: {_tile_list(p.tiles)}; {source}; "
        f"{'files from' if local else 'retrieved'} "
        f"{p.retrieved_at_utc.date().isoformat()}",
        file=out,
    )
    print(_privacy_note(p.dataset_id), file=out)

    print(file=out)
    print(f"  {'':<22}{'recorded':>12}{'DEM':>12}", file=out)
    print(
        f"  {'Ascent':<22}{m(c.ascent_recorded_m):>12}{m(c.ascent_reference_m):>12}",
        file=out,
    )
    print(
        f"  {'Descent':<22}{m(c.descent_recorded_m):>12}{m(c.descent_reference_m):>12}",
        file=out,
    )
    print(file=out)
    print("  Recorded minus DEM (calculated)", file=out)
    rows = [
        ("median (offset)", _diff(c.median_difference_m)),
        ("mean", _diff(c.mean_difference_m)),
        ("spread (std)", m(c.std_difference_m)),
        ("95 % within", f"+/- {m(c.p95_abs_difference_m)}"),
        (
            "largest",
            f"{m(c.max_abs_difference_m)} at km "
            f"{(c.max_abs_difference_at_m or 0) / 1000:.2f}",
        ),
        (
            "compared points",
            f"{c.compared_points} ({c.reference_coverage:.0%} with DEM)",
        ),
    ]
    for label, value in rows:
        print(f"    {label:<20}{value}", file=out)
    if c.drift is not None:
        d = c.drift
        print(
            f"    {'drift (smoothed)':<20}{_diff(d.start_m)} at start, "
            f"{_diff(d.end_m)} at end, range {m(d.range_m)} "
            f"(min {_diff(d.min_m)} at km {d.min_at_m / 1000:.1f}, "
            f"max {_diff(d.max_m)} at km {d.max_at_m / 1000:.1f})",
            file=out,
        )
    if c.warnings:
        warnings(c.warnings, out, "  ")
        if p.surface == "surface":
            print(
                "    Against a surface model, forest can cause part of this; "
                "a terrain model (e.g. BY_DGM1) is a better reference.",
                file=out,
            )

    _terrain_slope(v, out)

    print(file=out)
    print("  Notes", file=out)
    notes = [
        "Both series are calculated or observed values with their own errors; "
        "neither is ground truth.",
        "Ascent uses the same hysteresis threshold for both series.",
    ]
    if p.surface == "surface":
        notes.append(
            "This DEM is a surface model: in forest or near buildings it can show "
            "treetops or roofs rather than the ground."
        )
    for note in notes:
        print(f"    - {note}", file=out)
    print(f"  Attribution: {p.attribution}", file=out)


def enrich_batch(b: EnrichBatch, out: TextIO) -> None:
    profiles = [v.profile for v in b.enriched]
    if b.enriched:
        print(
            f"  {'ID':>4}{'ASCENT REC.':>13}{'ASCENT DEM':>12}{'MEDIAN DIFF':>13}"
            f"{'DRIFT':>9}  TILES",
            file=out,
        )
    for v in b.enriched:
        c = v.comparison
        tiles = (
            f"downloaded {', '.join(v.downloaded_tiles)}"
            if v.downloaded_tiles
            else "cached"
        )
        drift = "-" if c.drift is None else m(c.drift.range_m)
        flag = " !" if c.warnings else ""
        print(
            f"  {v.record.id:>4}{m(c.ascent_recorded_m):>13}"
            f"{m(c.ascent_reference_m):>12}{_diff(c.median_difference_m):>13}"
            f"{drift + flag:>9}  {tiles}",
            file=out,
        )
    if any(v.comparison.warnings for v in b.enriched):
        print(
            "  ! recorded elevation drifts against the DEM; details: "
            "alpha enrich ID --offline",
            file=out,
        )
    for f in b.failed:
        print(f"  #{f.track_id}: error: {f.message}", file=out)
    print(
        f"Enriched {len(b.enriched)}, failed {len(b.failed)}, "
        f"already stored {len(b.skipped_track_ids)} (use --refresh to sample again).",
        file=out,
    )
    if b.stopped:
        print(
            "Stopped after a failed download; the remaining tracks were not tried.",
            file=out,
        )
    if b.enriched:
        for note in dict.fromkeys(_privacy_note(p.dataset_id) for p in profiles):
            print(note, file=out)
        print("Details: alpha enrich ID --offline", file=out)
        attribution(profiles, out)


def _is_local(dataset_id: str) -> bool:
    dataset = DATASETS.get(dataset_id)
    return dataset is not None and dataset.source == "local"


def _privacy_note(dataset_id: str) -> str:
    if _is_local(dataset_id):
        return "Read from GeoTIFF tiles on this computer; nothing is sent anywhere."
    return (
        "Only fixed 0.25 degree tiles are requested from OpenTopography; the "
        "track itself is never sent."
    )


def _tile_list(tiles: Sequence[str], limit: int = 6) -> str:
    """Local 1 km tiles can be many; show a few and the count."""
    if len(tiles) <= limit:
        return ", ".join(tiles)
    return f"{', '.join(tiles[:limit])}, ... ({len(tiles)} tiles)"


def tile_plan(p: TilePlan, out: TextIO) -> None:
    print(f"{p.dataset.name}: {p.listed} tile(s) in the metalink", file=out)
    if p.for_tracks:
        print(
            f"Selected {len(p.selected)} tile(s) under your stored tracks "
            "(incl. neighbours where a track runs along a tile edge).",
            file=out,
        )
        if p.outside_metalink:
            print(
                f"  {p.outside_metalink} needed 1 km square(s) are not in the "
                "metalink; those track parts will have no terrain data.",
                file=out,
            )
        privacy = "The server learns which 1 km squares your tracks cross."
    else:
        print("Selected all tiles (use --for-tracks for fewer).", file=out)
        privacy = "The server learns only the region you chose for the metalink."
    print(
        f"Already present: {p.already_present}; to download: {p.to_download} "
        f"(sizes are not listed in the metalink) into {p.directory}",
        file=out,
    )
    print(f"From: {', '.join(p.hosts)} (HTTPS). {privacy}", file=out)


def _terrain_slope(v: ElevationView, out: TextIO) -> None:
    print(file=out)
    s = v.slope
    if s is None:
        print(f"  Terrain slope: unknown ({v.slope_note or 'not stored'})", file=out)
        return
    print(
        f"  Terrain slope (calculated over +/- {s.baseline_m:g} m: steepness of "
        "the ground around the track; not the track gradient or a hazard rating)",
        file=out,
    )
    total = sum(b.distance_m for b in s.bands) + s.unknown_m
    for b in s.bands:
        share = b.distance_m / total if total > 0 else 0.0
        print(
            f"    {b.label + ' deg':<12}{km(b.distance_m):>10}{share:>6.0%}", file=out
        )
    if s.unknown_m > 0:
        print(f"    {'unknown':<12}{km(s.unknown_m):>10}", file=out)
    if s.median_deg is not None and s.max_at_m is not None:
        print(
            f"    median {s.median_deg:.0f} deg, 90 % below {s.p90_deg:.0f} deg, "
            f"steepest {s.max_deg:.0f} deg at km {s.max_at_m / 1000:.2f}",
            file=out,
        )


# --- personal baseline (Phase 7) -----------------------------------------------------


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:+.0f} %"


def baseline(v: BaselineView, out: TextIO) -> None:
    f, s = v.fit, v.split
    ref = f.reference
    print(
        f"Personal speeds on DIN 33466 (reference: {ref.label}, "
        f"{ref.horizontal_speed_m_h / 1000:g} km/h, {ref.ascent_rate_m_h:g} m/h up, "
        f"{ref.descent_rate_m_h:g} m/h down)",
        file=out,
    )
    print(f"Elevation: {v.params.elevation}", file=out)
    print(file=out)
    split_by_track = {r.sample.track_id: r for r in s.samples}
    if f.samples:
        print(
            f"  {'ID':>4}  {'DATE':<11}{'DIST':>9}{'ASCENT':>8}{'MOVING':>9}"
            f"{'DIN STD':>9}{'V/H':>6}{'x OBS.':>8}{'LOO 1':>8}{'LOO 2':>8}  NAME",
            file=out,
        )
    for r in f.samples:
        x = r.sample
        sp = split_by_track[x.track_id]
        date = x.started_at_utc.strftime("%Y-%m-%d") if x.started_at_utc else "-"
        print(
            f"  {x.track_id:>4}  {date:<11}{km(x.distance_m):>9}{m(x.ascent_m):>8}"
            f"{hm(x.moving_time_s):>9}{hm(r.reference_moving_s):>9}"
            f"{sp.reference_vertical_s / sp.reference_horizontal_s:>6.2f}"
            f"{r.implied_multiplier:>8.2f}{_pct(r.error_ratio):>8}"
            f"{_pct(sp.error_ratio):>8}  {x.name or '(unnamed)'}",
            file=out,
        )
    for e in v.excluded:
        print(f"  #{e.track_id} not used: {e.reason}", file=out)
    print(file=out)
    print(
        "  V/H = vertical / horizontal time at the reference speeds; x OBS. = DIN "
        "standard moving time / your moving time (observed).",
        file=out,
    )
    print(
        "  LOO 1 / LOO 2 = error of the predicted moving time with that route left "
        "out: model 1 (one multiplier) / model 2 (flat and vertical).",
        file=out,
    )

    print(file=out)
    print("Model 1: one speed multiplier", file=out)
    if f.multiplier is None:
        print(
            f"  Not enough data: {f.n_groups} of {f.min_groups} distinct routes "
            f"({f.n_activities} activities). Nothing is fitted yet.",
            file=out,
        )
    else:
        assert f.average_interval and f.single_interval and f.validation
        assert f.assumptions
        lo, hi = f.average_interval
        slo, shi = f.single_interval
        print(
            f"  Fitted ({f.n_activities} activities, {f.n_groups} routes): "
            f"x{f.multiplier:.2f}; average x{lo:.2f}..x{hi:.2f}, one new hike "
            f"x{slo:.2f}..x{shi:.2f} (95 %)",
            file=out,
        )
        print(f"  = {_speeds(f.assumptions)} (source: fitted)", file=out)
        print(f"  {_validation(f.validation)}", file=out)

    print(file=out)
    print("Model 2: flat and vertical factors", file=out)
    if s.horizontal_factor is None:
        print(
            f"  {str(s.status).capitalize()}: {s.reason}. Nothing is fitted yet.",
            file=out,
        )
    else:
        assert s.horizontal_interval and s.vertical_interval and s.validation
        assert s.vertical_factor is not None and s.assumptions
        hlo, hhi = s.horizontal_interval
        vlo, vhi = s.vertical_interval
        print(
            f"  Fitted ({s.n_activities} activities, {s.n_groups} routes): "
            f"flat x{s.horizontal_factor:.2f} ({hlo:.2f}..{hhi:.2f}), "
            f"vertical x{s.vertical_factor:.2f} ({vlo:.2f}..{vhi:.2f}) (95 %)",
            file=out,
        )
        print(f"  = {_speeds(s.assumptions)} (source: fitted)", file=out)
        print(f"  {_validation(s.validation)}", file=out)
    if s.ratio_range is not None:
        print(
            f"  Route mix: V/H varies by x{s.ratio_range:.2f} across routes "
            f"(needs x{s.min_ratio_range:g} to separate flat from vertical).",
            file=out,
        )
    _effort(v, out)
    if f.validation and s.validation:
        better = (
            "model 2"
            if s.validation.median_abs_error < f.validation.median_abs_error
            else "model 1"
        )
        print(file=out)
        print(
            f"Comparison on the same activities: median error model 1 "
            f"{f.validation.median_abs_error:.0%}, model 2 "
            f"{s.validation.median_abs_error:.0%}; {better} predicts better.",
            file=out,
        )


def _speeds(a: DinAssumptions) -> str:
    return (
        f"{a.horizontal_speed_m_h / 1000:.1f} km/h, {a.ascent_rate_m_h:.0f} m/h up, "
        f"{a.descent_rate_m_h:.0f} m/h down"
    )


def _validation(v: Validation) -> str:
    return (
        f"Leave-one-route-out error of the moving time: median "
        f"{v.median_abs_error:.0%}, max {v.max_abs_error:.0%} ({v.folds} folds)"
    )


def _effort(v: BaselineView, out: TextIO) -> None:
    if not v.effort_groups:
        return
    print(file=out)
    print(
        f"Effort (your annotation; models fitted on '{v.baseline_params.effort}')",
        file=out,
    )
    for g in v.effort_groups:
        print(
            f"  {g.effort or 'not stated'!s:<12}{g.n_activities:>3} activities, "
            f"observed x{g.mean_implied_multiplier:.2f} vs DIN standard",
            file=out,
        )
    print(
        "  Observed averages only: the efforts may also differ in terrain.",
        file=out,
    )
    for p in v.outside_effort:
        x = p.sample
        parts = []
        if p.vs_multiplier is not None:
            parts.append(f"model 1 {_pct(p.vs_multiplier - 1)}")
        if p.vs_split is not None:
            parts.append(f"model 2 {_pct(p.vs_split - 1)}")
        compared = (
            "faster(+)/slower(-) than predicted: " + ", ".join(parts)
            if parts
            else "no fitted model to compare with yet"
        )
        print(
            f"  #{x.track_id} {x.name or '(unnamed)'} [{x.effort or 'not stated'}]: "
            f"{compared}",
            file=out,
        )
