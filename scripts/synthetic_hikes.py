"""Generate synthetic hikes with a known pace (ADR-021).

Two sources of paths:

-   ``--from-profile default`` (the default): variants of the activities in
    your personal profile: original path, reversed, stretched, steeper,
    flatter, and one-way up to the high point. The paths contain your real
    coordinates, so the files stay in ``data/local/`` (git-ignored) and must
    never be committed or shared.
-   ``--invented``: loops drawn from random numbers around an arbitrary
    point; no personal data at all.

Timestamps come from a hiker whose speeds are known: DIN 33466 with a flat
factor ``kh`` and a climbing factor ``kv``, a lognormal day-to-day factor,
local pace changes, a few stops, and GPS and barometer noise. Some hikes are
"hard" efforts (faster). Every file has the GPX type ``synthetic-hiking`` and
a neutral name; start dates are invented (from 2030 on).

``--import-into NAME`` stores them in a synthetic profile (created if
missing) and sets kind and effort. A personal profile is refused: synthetic
hikes never enter real fits. ``manifest.json`` records the true speeds and
draws, so a fit can be checked against the truth.

    python scripts/synthetic_hikes.py --invented --count 12 --import-into dev
    python scripts/synthetic_hikes.py --import-into dev        # your paths
    alpha --profile dev baseline
"""

import argparse
import json
import math
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from xml.sax.saxutils import escape

import numpy as np
from pyproj import Geod

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "local"
GEOD = Geod(ellps="WGS84")
EARTH_M_PER_DEG = 111_320.0
H, U, D = 4000.0, 300.0, 500.0  # DIN 33466 reference speeds, m/h
SYNTHETIC_TYPE = "synthetic-hiking"  # alpha.application.profiles
VARIANTS = ("original", "reversed", "stretched", "steeper", "flatter", "one-way")


@dataclass(frozen=True)
class Path3:
    lat: np.ndarray
    lon: np.ndarray
    ele: np.ndarray


@dataclass(frozen=True)
class Truth:
    file: str
    source: str  # "invented" or "profile track <id>"
    variant: str
    kh: float
    kv: float
    day_factor: float  # multiplies the moving time
    effort: str  # "normal" or "hard"
    moving_s: float  # generated moving time (stops excluded)
    stops_s: float
    distance_m: float  # of the generated path, before GPS noise
    ascent_m: float


# --- paths ------------------------------------------------------------------------


def _to_xy(p: Path3) -> tuple[np.ndarray, np.ndarray, float, float]:
    lat0, lon0 = float(p.lat[0]), float(p.lon[0])
    x = (p.lon - lon0) * EARTH_M_PER_DEG * math.cos(math.radians(lat0))
    y = (p.lat - lat0) * EARTH_M_PER_DEG
    return x, y, lat0, lon0


def _from_xy(
    x: np.ndarray, y: np.ndarray, lat0: float, lon0: float
) -> tuple[np.ndarray, np.ndarray]:
    lat = lat0 + y / EARTH_M_PER_DEG
    lon = lon0 + x / (EARTH_M_PER_DEG * math.cos(math.radians(lat0)))
    return lat, lon


def variant(p: Path3, name: str) -> Path3:
    """A geometric variant; elevation scales around the lowest point."""
    low = float(np.nanmin(p.ele))
    if name == "original":
        return p
    if name == "reversed":
        return Path3(p.lat[::-1], p.lon[::-1], p.ele[::-1])
    if name == "one-way":
        top = int(np.nanargmax(p.ele))
        top = max(top, 2)
        return Path3(p.lat[: top + 1], p.lon[: top + 1], p.ele[: top + 1])
    scale_xy, scale_z = {
        "stretched": (1.3, 1.0),
        "steeper": (0.85, 1.3),
        "flatter": (1.0, 0.3),
    }[name]
    x, y, lat0, lon0 = _to_xy(p)
    lat, lon = _from_xy(x * scale_xy, y * scale_xy, lat0, lon0)
    return Path3(lat, lon, low + (p.ele - low) * scale_z)


def smoothed(p: Path3, window_m: float = 30.0, spacing_m: float = 5.0) -> Path3:
    """The recorded path without its own GPS jitter: positions averaged over
    ~``window_m``, then thinned to ``spacing_m``. Otherwise the jitter of
    the source recording would count as distance in the "true" path."""
    x, y, lat0, lon0 = _to_xy(p)
    steps = np.hypot(np.diff(x), np.diff(y))
    k = max(int(window_m / max(float(np.median(steps)), 0.1)) | 1, 1)
    kernel = np.ones(k) / k
    norm = np.convolve(np.ones(len(x)), kernel, mode="same")
    xs = np.convolve(x, kernel, mode="same") / norm
    ys = np.convolve(y, kernel, mode="same") / norm
    keep = [0]
    for i in range(1, len(xs)):
        if math.hypot(xs[i] - xs[keep[-1]], ys[i] - ys[keep[-1]]) >= spacing_m:
            keep.append(i)
    idx = np.array(keep)
    lat, lon = _from_xy(xs[idx], ys[idx], lat0, lon0)
    return Path3(lat, lon, p.ele[idx])


def invented_path(rng: np.random.Generator) -> Path3:
    """A loop of 6-16 km with one or two hills, around an arbitrary point."""
    length = rng.uniform(6000, 16000)
    n = int(length / 10)
    t = np.linspace(0, 2 * math.pi, n)
    radius = length / (2 * math.pi)
    wobble = 1 + 0.15 * np.sin(3 * t + rng.uniform(0, 6))
    x, y = radius * wobble * np.cos(t), radius * wobble * np.sin(t)
    hills = int(rng.integers(1, 3))
    # Mean gradient at most ~18 % (up and down), steepest ~28 %.
    climb = min(rng.uniform(200, 1300), 0.09 * length / hills)
    ele = 800 + climb * (0.5 - 0.5 * np.cos(hills * t))
    lat, lon = _from_xy(x - x[0], y - y[0], 46.0, 8.0)
    return Path3(lat, lon, ele)


def profile_paths(base: Path, profile: str) -> list[tuple[str, Path3]]:
    """Activities of a personal profile (hiking/walking with elevation)."""
    from alpha.application.profiles import open_profile
    from alpha.application.tracks import Workspace
    from alpha.domain.track import TrackKind

    p = open_profile(base, profile)
    if p.synthetic:
        raise SystemExit(f"profile {profile!r} is synthetic; use a personal one")
    ws = Workspace(p.data_dir)
    out = []
    try:
        with ws.db.transaction() as repo:
            for record in repo.list_tracks():
                if record.kind is not TrackKind.ACTIVITY:
                    continue
                if record.activity_type not in ("hiking", "walking"):
                    continue
                track = repo.load_track(record.id)
                keep = np.isfinite(track.ele_raw_m)
                if keep.sum() < 10:
                    continue
                raw = Path3(
                    np.asarray(track.lat_deg[keep]),
                    np.asarray(track.lon_deg[keep]),
                    np.asarray(track.ele_raw_m[keep]),
                )
                out.append((f"profile track {record.id}", smoothed(raw)))
    finally:
        ws.close()
    return out


# --- timing ---------------------------------------------------------------------


def _hysteresis_steps(ele: np.ndarray, threshold: float = 3.0) -> np.ndarray:
    """Per-step elevation change after a 3 m hysteresis (like ADR-004), so
    noise in the source path does not count as climbing."""
    out = np.zeros(len(ele) - 1)
    ref = ele[0]
    last = 0
    for i in range(1, len(ele)):
        if abs(ele[i] - ref) >= threshold:
            span = ele[i] - ref
            out[last:i] += span / (i - last)
            ref, last = ele[i], i
    return out


def schedule(
    p: Path3, kh: float, kv: float, factor: float, rng: np.random.Generator
) -> tuple[np.ndarray, float, float, float]:
    """Moving time at every point (s from start), and distance, ascent, and
    total moving time. DIN 33466 for the whole hike, spread over the steps
    by DIN's rule applied to each step, with local pace noise."""
    _, _, d = GEOD.inv(p.lon[:-1], p.lat[:-1], p.lon[1:], p.lat[1:])
    d = np.asarray(d)
    dz = _hysteresis_steps(p.ele)
    up, down = np.clip(dz, 0, None), np.clip(-dz, 0, None)
    h = d / (H * kh) * 3600
    v = (up / U + down / D) / kv * 3600
    total = (max(h.sum(), v.sum()) + min(h.sum(), v.sum()) / 2) * factor
    local = np.maximum(h, v) + np.minimum(h, v) / 2  # DIN rule per step
    weight = local * np.exp(_smooth_noise(len(d), 0.15, 50, rng))
    step_s = weight / weight.sum() * total
    return (
        np.concatenate([[0.0], np.cumsum(step_s)]),
        float(d.sum()),
        float(up.sum()),
        float(total),
    )


def _smooth_noise(
    n: int, sigma: float, width: int, rng: np.random.Generator
) -> np.ndarray:
    raw = rng.normal(0, 1, n + width)
    smooth = np.convolve(raw, np.ones(width) / math.sqrt(width), mode="valid")[:n]
    return sigma * smooth


def sample_points(
    p: Path3,
    moving_at: np.ndarray,
    interval_s: float,
    rng: np.random.Generator,
) -> tuple[list[tuple[float, float, float, float]], float]:
    """(lat, lon, ele, seconds) every ``interval_s`` seconds, with 2-4 stops,
    GPS noise (~3 m, correlated over ~5 min like a phone or watch), and
    barometer noise (~0.3 m)."""
    n_stops = int(rng.integers(2, 5))
    stop_at = np.sort(rng.uniform(0.15, 0.9, n_stops)) * moving_at[-1]
    stop_len = rng.uniform(120, 900, n_stops)
    clock: list[float] = []  # wall-clock seconds
    moving: list[float] = []  # moving seconds at that moment
    t_wall, t_move, k = 0.0, 0.0, 0
    while t_move <= moving_at[-1]:
        clock.append(t_wall)
        moving.append(t_move)
        if k < n_stops and t_move >= stop_at[k]:
            for s in np.arange(interval_s, stop_len[k], interval_s):
                clock.append(t_wall + s)
                moving.append(t_move)
            t_wall += float(stop_len[k])
            k += 1
        t_wall += interval_s
        t_move += interval_s
    m = np.array(moving)
    lat = np.interp(m, moving_at, p.lat)
    lon = np.interp(m, moving_at, p.lon)
    ele = np.interp(m, moving_at, p.ele)
    n = len(m)
    width = max(int(300 / interval_s), 2)
    gps = _smooth_noise(n, 3.0, width, rng), _smooth_noise(n, 3.0, width, rng)
    lat = lat + gps[0] / EARTH_M_PER_DEG
    lon = lon + gps[1] / (EARTH_M_PER_DEG * math.cos(math.radians(float(lat[0]))))
    ele = ele + rng.normal(0, 0.3, n)
    points = list(zip(lat.tolist(), lon.tolist(), ele.tolist(), clock, strict=True))
    return points, float(stop_len.sum())


def gpx_text(name: str, start: datetime, points: Sequence[tuple[float, ...]]) -> str:
    rows = []
    for lat, lon, ele, s in points:
        when = (start + timedelta(seconds=s)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows.append(
            f'<trkpt lat="{lat:.7f}" lon="{lon:.7f}"><ele>{ele:.1f}</ele>'
            f"<time>{when}</time></trkpt>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<gpx version="1.1" creator="alpha-synthetic" '
        'xmlns="http://www.topografix.com/GPX/1/1">'
        f"<trk><name>{escape(name)}</name><type>{SYNTHETIC_TYPE}</type><trkseg>\n"
        + "\n".join(rows)
        + "\n</trkseg></trk></gpx>\n"
    )


# --- main -----------------------------------------------------------------------


def generate(
    paths: Sequence[tuple[str, Path3]],
    variants: Sequence[str],
    out_dir: Path,
    kh: float,
    kv: float,
    sigma: float,
    hard_share: float,
    interval_s: float,
    seed: int,
) -> list[Truth]:
    rng = np.random.default_rng(seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    truths = []
    start = datetime(2030, 1, 1, 8, 0, tzinfo=UTC)
    number = 0
    for source, base in paths:
        for name in variants:
            if name == "one-way" and np.nanargmax(base.ele) < 0.2 * len(base.ele):
                continue  # the high point is near the start: nothing to walk up
            path = variant(base, name)
            number += 1
            hard = rng.random() < hard_share
            factor = math.exp(rng.normal(0, sigma)) / (1.15 if hard else 1.0)
            moving_at, dist, ascent, total = schedule(path, kh, kv, factor, rng)
            points, stops = sample_points(path, moving_at, interval_s, rng)
            label = f"Synthetic S{number:02d} ({name})"
            file = f"synthetic_s{number:02d}_{name}.gpx"
            day = start + timedelta(days=7 * number)
            (out_dir / file).write_text(gpx_text(label, day, points), encoding="utf-8")
            truths.append(
                Truth(
                    file,
                    source,
                    name,
                    kh,
                    kv,
                    round(factor, 4),
                    "hard" if hard else "normal",
                    round(total, 1),
                    round(stops, 1),
                    round(dist, 1),
                    round(ascent, 1),
                )
            )
    return truths


def import_into(
    base: Path, profile: str, out_dir: Path, truths: Sequence[Truth]
) -> None:
    from alpha.application.profiles import ProfileError, create_profile, open_profile
    from alpha.application.tracks import Workspace, edit_track, import_file
    from alpha.domain.track import Effort, TrackKind

    try:
        p = open_profile(base, profile)
    except ProfileError:
        p = create_profile(base, profile, synthetic=True)
        print(f"created synthetic profile {profile}")
    if not p.synthetic:
        raise SystemExit(
            f"profile {profile!r} is personal: synthetic hikes are never mixed "
            "into real data"
        )
    ws = Workspace(p.data_dir, synthetic=True)
    try:
        for t in truths:
            outcome = import_file(ws, t.file, (out_dir / t.file).read_bytes())
            for record in outcome.tracks:
                edit_track(
                    ws, record.id, kind=TrackKind.ACTIVITY, effort=Effort(t.effort)
                )
    finally:
        ws.close()
    print(f"imported {len(truths)} hikes into profile {profile}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--invented", action="store_true", help="no personal data")
    source.add_argument("--from-profile", default="default")
    parser.add_argument("--count", type=int, default=10, help="invented paths")
    parser.add_argument(
        "--variants", default=",".join(VARIANTS), help="for profile paths"
    )
    parser.add_argument("--kh", type=float, default=1.3, help="true flat factor")
    parser.add_argument("--kv", type=float, default=1.6, help="true climbing factor")
    parser.add_argument("--sigma", type=float, default=0.06, help="day-to-day spread")
    parser.add_argument("--hard-share", type=float, default=0.15)
    parser.add_argument("--interval-s", type=float, default=2.0)
    parser.add_argument("--seed", type=int, default=21)
    parser.add_argument("--data-dir", type=Path, default=DATA)
    parser.add_argument("--out", type=Path, help="default: <data-dir>/synthetic")
    parser.add_argument("--import-into", metavar="PROFILE")
    args = parser.parse_args(argv)
    args.out = args.out or args.data_dir / "synthetic"

    if args.invented:
        rng = np.random.default_rng(args.seed + 1)
        paths = [("invented", invented_path(rng)) for _ in range(args.count)]
        variants: Sequence[str] = ("original",)
    else:
        paths = profile_paths(args.data_dir, args.from_profile)
        variants = [v for v in args.variants.split(",") if v]
        unknown = set(variants) - set(VARIANTS)
        if unknown:
            parser.error(f"unknown variants: {', '.join(sorted(unknown))}")
        if not paths:
            print("no hiking activities in that profile; try --invented")
            return 1
    truths = generate(
        paths,
        variants,
        args.out,
        args.kh,
        args.kv,
        args.sigma,
        args.hard_share,
        args.interval_s,
        args.seed,
    )
    manifest = {
        "note": "synthetic hikes; true speeds below. Derived from personal "
        "paths unless source is 'invented': never commit or share.",
        "reference_m_h": {"horizontal": H, "ascent": U, "descent": D},
        "hikes": [asdict(t) for t in truths],
    }
    (args.out / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    print(f"wrote {len(truths)} synthetic hikes to {args.out}")
    if args.import_into:
        import_into(args.data_dir, args.import_into, args.out, truths)
    return 0


if __name__ == "__main__":
    sys.exit(main())
