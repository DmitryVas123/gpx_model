"""Simulation study of the personal speed models (ADR-021).

Fits models 1 and 2 (``alpha.modelling``) on simulated hikers whose true
speeds are known, and measures what real data cannot tell us yet:

-   how often model 2 is fitted, and how close its factors come to the truth,
    for a given number of routes and route mix;
-   whether its 95 % intervals contain the truth about 95 % of the time;
-   which model predicts new typical, steep, and flat routes better;
-   how much unannotated hard efforts bias the fit.

Route shapes (distance, ascent, descent) come from the activities in your
default profile's personal fit when there are any, otherwise from a small
invented set. Only aggregates are printed: no shapes, names, or dates.
Nothing is written. The study uses plain samples, not GPX files, so it says
nothing about GPS noise or moving-time detection.

    python scripts/simulate_baseline.py              # all experiments
    python scripts/simulate_baseline.py --reps 50    # faster, coarser
    python scripts/simulate_baseline.py --invented   # ignore personal data
"""

import argparse
import math
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from alpha.modelling import (
    ActivitySample,
    BaselineParams,
    FitStatus,
    fit_speed_multiplier,
    fit_split_speeds,
)

ROOT = Path(__file__).resolve().parents[1]
REF = BaselineParams().reference
H, U, D = REF.horizontal_speed_m_h, REF.ascent_rate_m_h, REF.descent_rate_m_h

# Invented loop shapes (distance m, ascent m), for a fresh clone.
INVENTED = ((9000, 600), (12000, 900), (14000, 1100), (7000, 700), (16000, 800))

Shape = tuple[float, float, float]  # distance, ascent, descent


@dataclass(frozen=True)
class Hiker:
    label: str
    kh: float  # true flat speed factor
    kv: float  # true climbing factor
    sigma: float  # day-to-day spread of ln(moving time)


def din_moving_s(shape: Shape, kh: float, kv: float) -> float:
    d, up, down = shape
    h = d / (H * kh) * 3600
    v = (up / U + down / D) / kv * 3600
    return max(h, v) + min(h, v) / 2


def ratio(shape: Shape) -> float:
    d, up, down = shape
    return (up / U + down / D) / (d / H)


# --- route mixes -------------------------------------------------------------------


def similar(base: Shape, rng: np.random.Generator) -> Shape:
    """A route like one already walked: distance and climb vary by ~20 %."""
    d, up, down = base
    fd, fa = np.exp(rng.normal(0, 0.2, 2))
    return d * fd, up * fa, down * fa


def steep(base: Shape, rng: np.random.Generator) -> Shape:
    d, up, down = similar(base, rng)
    return d * 0.6, up * 1.6, down * 1.6


def flat(base: Shape, rng: np.random.Generator) -> Shape:
    d, up, down = similar(base, rng)
    return d * 1.2, up * 0.2, down * 0.2


def one_way_up(base: Shape, rng: np.random.Generator) -> Shape:
    """Up on foot, down by lift: little descent."""
    d, up, down = similar(base, rng)
    return d * 0.6, up, down * 0.1


Variant = Callable[[Shape, np.random.Generator], Shape]
MIXES: dict[str, tuple[Variant, ...]] = {
    # The first entries replace "similar" routes; the rest are similar.
    "similar only": (),
    "+2 steep": (steep, steep),
    "+2 flat": (flat, flat),
    "+1 steep +1 flat": (steep, flat),
    "+2 one-way up": (one_way_up, one_way_up),
}


def draw_routes(
    bases: Sequence[Shape], n: int, mix: str, rng: np.random.Generator
) -> list[Shape]:
    special = MIXES[mix]
    routes = []
    for i in range(n):
        base = bases[int(rng.integers(len(bases)))]
        variant = special[i] if i < len(special) else similar
        routes.append(variant(base, rng))
    return routes


def samples_for(
    routes: Sequence[Shape],
    hiker: Hiker,
    rng: np.random.Generator,
    hard_share: float = 0.0,
    hard_factor: float = 1.15,
) -> list[ActivitySample]:
    out = []
    for i, shape in enumerate(routes):
        t = din_moving_s(shape, hiker.kh, hiker.kv) * math.exp(
            rng.normal(0, hiker.sigma)
        )
        if rng.random() < hard_share:  # pushed hard, but not annotated
            t /= hard_factor
        d, up, down = shape
        out.append(ActivitySample(i + 1, i + 1, None, None, d, up, down, t))
    return out


# --- measurements -----------------------------------------------------------------


@dataclass
class Tally:
    runs: int = 0
    split_fitted: int = 0
    split_not_identifiable: int = 0
    kh_log_err: list[float] | None = None
    kv_log_err: list[float] | None = None
    kh_covered: int = 0
    kv_covered: int = 0
    k1_covered: int = 0  # model 1 average interval vs. the true kh = kv
    k1_runs: int = 0
    pred_err: dict[str, dict[str, list[float]]] | None = None  # model -> route -> |err|

    def __post_init__(self) -> None:
        self.kh_log_err, self.kv_log_err = [], []
        self.pred_err = {m: {r: [] for r in TEST_ROUTES} for m in ("1", "2", "chosen")}


TEST_ROUTES: dict[str, Variant] = {
    "typical": similar,
    "steep": steep,
    "flat": flat,
}


def run_case(
    bases: Sequence[Shape],
    hiker: Hiker,
    n: int,
    mix: str,
    reps: int,
    seed: int,
    hard_share: float = 0.0,
) -> Tally:
    rng = np.random.default_rng(seed)
    tally = Tally()
    params = BaselineParams()
    tests = {
        name: [variant(bases[i % len(bases)], rng) for i in range(20)]
        for name, variant in TEST_ROUTES.items()
    }
    for _ in range(reps):
        routes = draw_routes(bases, n, mix, rng)
        samples = samples_for(routes, hiker, rng, hard_share)
        one = fit_speed_multiplier(samples, params)
        two = fit_split_speeds(samples, params)
        tally.runs += 1
        if two.status is FitStatus.NOT_IDENTIFIABLE:
            tally.split_not_identifiable += 1
        if hiker.kh == hiker.kv and one.average_interval is not None:
            tally.k1_runs += 1
            lo, hi = one.average_interval
            tally.k1_covered += lo <= hiker.kh <= hi
        k2 = None
        if two.horizontal_factor is not None and two.vertical_factor is not None:
            tally.split_fitted += 1
            kh, kv = two.horizontal_factor, two.vertical_factor
            k2 = (kh, kv)
            assert tally.kh_log_err is not None and tally.kv_log_err is not None
            tally.kh_log_err.append(abs(math.log(kh / hiker.kh)))
            tally.kv_log_err.append(abs(math.log(kv / hiker.kv)))
            if two.horizontal_interval and two.vertical_interval:
                lo, hi = two.horizontal_interval
                tally.kh_covered += lo <= hiker.kh <= hi
                lo, hi = two.vertical_interval
                tally.kv_covered += lo <= hiker.kv <= hi
        if one.multiplier is None:
            continue
        k1 = one.multiplier
        # The model a scenario would use: smaller leave-one-route-out error.
        chosen = "1"
        if (
            k2
            and two.validation
            and one.validation
            and two.validation.median_abs_error < one.validation.median_abs_error
        ):
            chosen = "2"
        assert tally.pred_err is not None
        for name, shapes in tests.items():
            for shape in shapes:
                truth = din_moving_s(shape, hiker.kh, hiker.kv)
                e1 = abs(din_moving_s(shape, k1, k1) / truth - 1)
                tally.pred_err["1"][name].append(e1)
                e2 = abs(din_moving_s(shape, *k2) / truth - 1) if k2 else e1
                tally.pred_err["2"][name].append(e2)
                tally.pred_err["chosen"][name].append(e2 if chosen == "2" else e1)
    return tally


def pct(values: Sequence[float], q: float = 50) -> str:
    return "  -  " if not values else f"{np.percentile(values, q) * 100:4.1f}%"


def share(count: int, total: int) -> str:
    return "  -  " if total == 0 else f"{count / total * 100:3.0f}%"


# --- inputs -----------------------------------------------------------------------


def personal_shapes() -> tuple[list[Shape], float | None]:
    """Shapes and model 1 spread from the default profile's fit, if any."""
    from alpha.application.baseline import personal_baseline
    from alpha.application.tracks import Workspace

    data = ROOT / "data" / "local"
    if not (data / "alpha.db").is_file():
        return [], None
    ws = Workspace(data)
    try:
        view = personal_baseline(ws)
    finally:
        ws.close()
    shapes = [
        (r.sample.distance_m, r.sample.ascent_m, r.sample.descent_m)
        for r in view.fit.samples
    ]
    return shapes, view.fit.spread


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--reps", type=int, default=200)
    parser.add_argument("--seed", type=int, default=21)
    parser.add_argument("--invented", action="store_true")
    args = parser.parse_args(argv)

    shapes, spread = ([], None) if args.invented else personal_shapes()
    if shapes:
        source = f"{len(shapes)} route shapes from your fitted activities"
    else:
        shapes = [(float(d), float(a), float(a)) for d, a in INVENTED]
        source = f"{len(shapes)} invented route shapes"
    sigma = spread if spread else 0.06
    ratios = sorted(ratio(s) for s in shapes)
    print(f"Route shapes: {source}; DIN v/h ratio {ratios[0]:.2f}-{ratios[-1]:.2f}")
    print(f"Day-to-day spread sigma = {sigma:.3f} (ln moving time)")
    print(f"{args.reps} simulated hikers per row; seed {args.seed}\n")

    hikers = (
        Hiker("one factor (kh = kv = 1.45)", 1.45, 1.45, sigma),
        Hiker("strong climber (kh 1.25, kv 1.70)", 1.25, 1.70, sigma),
    )

    for hiker in hikers:
        print(f"== Hiker: {hiker.label}")
        print(
            f"  {'routes':>6}  {'mix':<18}{'M2 fit':>7}{'n.ident':>8}"
            f"{'|kh err|':>9}{'|kv err|':>9}{'kh cov':>7}{'kv cov':>7}"
            f"{'M1 cov':>7}   pred. error (median) chosen model: typ/steep/flat"
        )
        for n in (5, 8, 10, 12, 15, 20):
            for mix in MIXES:
                if n < 8 and mix != "similar only":
                    continue
                t = run_case(shapes, hiker, n, mix, args.reps, args.seed + n)
                assert t.kh_log_err is not None and t.kv_log_err is not None
                assert t.pred_err is not None
                chosen = "/".join(pct(t.pred_err["chosen"][r]) for r in TEST_ROUTES)
                print(
                    f"  {n:>6}  {mix:<18}{share(t.split_fitted, t.runs):>7}"
                    f"{share(t.split_not_identifiable, t.runs):>8}"
                    f"{pct(t.kh_log_err):>9}{pct(t.kv_log_err):>9}"
                    f"{share(t.kh_covered, t.split_fitted):>7}"
                    f"{share(t.kv_covered, t.split_fitted):>7}"
                    f"{share(t.k1_covered, t.k1_runs):>7}   {chosen}"
                )
        print()

    print("== Unannotated hard efforts (pace x1.15) vs. model 1, 10 similar routes")
    hiker = hikers[0]
    for hard in (0.0, 0.1, 0.2, 0.3):
        rng = np.random.default_rng(args.seed)
        ks = []
        for _ in range(args.reps):
            routes = draw_routes(shapes, 10, "similar only", rng)
            fit = fit_speed_multiplier(samples_for(routes, hiker, rng, hard))
            if fit.multiplier is not None:
                ks.append(fit.multiplier)
        bias = np.median(ks) / hiker.kh - 1
        print(f"  {hard * 100:3.0f}% hard: median fitted k bias {bias * 100:+5.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
