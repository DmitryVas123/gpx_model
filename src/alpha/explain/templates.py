"""Deterministic explanations from ALPHA reports (ADR-020).

Each topic turns one JSON report into a ``FactSet`` and a list of
statements. Every statement names the facts it uses. The text never states
a value that is not a fact, and never says anything about safety, weather,
snow, route conditions, or whether to go. These templates are the complete
explanation without a language model, and the draft and fallback with one.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from alpha.explain.facts import FactSet


@dataclass(frozen=True)
class Statement:
    text: str
    facts: tuple[str, ...]  # keys of the facts the text uses
    # A qualification the reader must not miss (what a number does and does
    # not mean). Never reworded by a model: always shown verbatim.
    caveat: bool = False


Draft = tuple[FactSet, tuple[Statement, ...]]

# Shown after every explanation, outside anything a model writes.
BOUNDARY = (
    "ALPHA explains its own calculations only. Real hiking time also depends "
    "on things ALPHA does not know, such as the conditions on the day, and "
    "ALPHA does not judge whether a route is safe."
)


def _say(text: str, *keys: str | None) -> Statement:
    return Statement(text, tuple(k for k in keys if k))


def _caveat(text: str) -> Statement:
    return Statement(text, (), caveat=True)


SYNTHETIC_CAVEAT = (
    "These speeds are fitted on generated test hikes, not on real recorded "
    "activities, so they describe no real person."
)


# --- scenarios ---------------------------------------------------------------------


def scenarios(report: dict[str, Any]) -> Draft:
    f = FactSet()
    out: list[Statement] = []
    a, inputs, personal = report["assumptions"], report["inputs"], report["personal"]
    by_label = {s["label"]: s for s in report["scenarios"]}
    base = by_label.get("baseline") or report["scenarios"][0]

    f.add("method", "duration method", "DIN 33466", "text", "label")
    src = f.add(
        "assumptions.source", "where the speeds come from", a["source"], "text", "label"
    )
    h = f.add(
        "speed.horizontal",
        "horizontal speed",
        a["horizontal_speed_m_h"],
        "km/h",
        _prov(a),
    )
    up = f.add("speed.ascent", "ascent rate", a["ascent_rate_m_h"], "m/h", _prov(a))
    down = f.add(
        "speed.descent", "descent rate", a["descent_rate_m_h"], "m/h", _prov(a)
    )
    kind = {
        "preset": "the DIN 33466 standard speeds for an average hiker",
        "user": "the speeds you entered",
        "fitted": "speeds fitted from your own activities",
    }[a["source"]]
    out.append(
        _say(
            f"The estimate uses the DIN 33466 method with {kind}: "
            f"{f.show(h)} on the flat, {f.show(up)} uphill and {f.show(down)} "
            f"downhill."
            if h and up and down
            else "",
            "method",
            src,
            h,
            up,
            down,
        )
    )
    if personal:
        n = f.add(
            "personal.activities",
            "activities in the fit",
            personal["n_activities"],
            "count",
            "fitted",
        )
        r = f.add(
            "personal.routes",
            "routes in the fit",
            personal["n_routes"],
            "count",
            "fitted",
        )
        err = f.add(
            "personal.error",
            "typical prediction error (leave one route out)",
            personal["loo_median_abs_error"],
            "percent",
            "fitted",
        )
        f.add(
            "personal.effort",
            "efforts the fit uses",
            personal["effort"],
            "text",
            "label",
        )
        assert n and r and err
        out.append(
            _say(
                f"These speeds come from {f.show(n)} of your activities on "
                f"{f.show(r)} routes with {personal['effort']} effort; with one "
                f"route left out, the prediction was typically off by "
                f"{f.show(err)}.",
                n,
                r,
                err,
                "personal.effort",
            )
        )
        if personal.get("synthetic_data"):
            out.append(_caveat(SYNTHETIC_CAVEAT))

    d = f.add("route.distance", "distance", inputs["distance_m"], "km", "calculated")
    asc = f.add("route.ascent", "ascent", inputs["ascent_m"], "m", "calculated")
    desc = f.add("route.descent", "descent", inputs["descent_m"], "m", "calculated")
    if d and asc and desc:
        out.append(
            _say(
                f"The route is {f.show(d)} long with {f.show(asc)} of ascent and "
                f"{f.show(desc)} of descent.",
                d,
                asc,
                desc,
            )
        )

    b = base["breakdown_s"]
    th = f.add(
        "estimate.baseline.horizontal",
        "time for the distance",
        b["horizontal"],
        "duration",
        "estimated",
    )
    tv = f.add(
        "estimate.baseline.vertical",
        "time for ascent and descent",
        b["vertical"],
        "duration",
        "estimated",
    )
    tm = f.add(
        "estimate.baseline.moving", "moving time", b["moving"], "duration", "estimated"
    )
    if th and tv and tm:
        larger = (
            "the climbing and descending part is larger"
            if b["vertical"] >= b["horizontal"]
            else "the distance part is larger"
        )
        out.append(
            _say(
                f"At this pace the distance alone takes {f.show(th)} and the "
                f"ascent and descent {f.show(tv)}. DIN 33466 counts the larger "
                f"part fully and the smaller one by half, which gives "
                f"{f.show(tm)} of moving time; {larger}.",
                th,
                tv,
                tm,
            )
        )
    breaks = b["break"] + b["delay"]
    if breaks > 0:
        tb = f.add("estimate.breaks", "breaks and delay", breaks, "duration", "label")
        tt = f.add(
            "estimate.baseline.total",
            "total time",
            base["estimated_duration_s"],
            "duration",
            "estimated",
        )
        assert tb and tt
        out.append(
            _say(
                f"With {f.show(tb)} of breaks and delay the total is {f.show(tt)}.",
                tb,
                tt,
            )
        )

    fast, slow = by_label.get("faster"), by_label.get("slower")
    if fast and slow:
        tf = f.add(
            "estimate.faster.total",
            "total time, faster scenario",
            fast["estimated_duration_s"],
            "duration",
            "estimated",
        )
        ts = f.add(
            "estimate.slower.total",
            "total time, slower scenario",
            slow["estimated_duration_s"],
            "duration",
            "estimated",
        )
        assert tf and ts
        meaning = (
            "your own variation between hikes (the range a single new hike "
            "fell into in 95 % of cases)"
            if personal
            else "the same speeds made faster or slower by a fixed factor"
        )
        out.append(
            _say(
                f"The range runs from {f.show(tf)} to {f.show(ts)}; it reflects "
                f"{meaning}.",
                tf,
                ts,
            )
        )

    for i, o in enumerate(report["observed"]):
        if o["moving_time_s"] is None:
            continue
        om = f.add(
            f"observed.{i}.moving",
            "recorded moving time",
            o["moving_time_s"],
            "duration",
            "observed",
        )
        k = f.add(
            f"observed.{i}.factor",
            "speed factor that matches the recorded time",
            o["implied_speed_multiplier"],
            "factor",
            "observed",
        )
        who = "This activity" if o["relation"] == "same track" else "A linked activity"
        text = (
            f"{who} was recorded with {f.show(om) if om else 'an unknown'} moving time"
        )
        if k:
            k_val = float(f[k].value)
            pace = (
                "faster than"
                if k_val > 1.02
                else "slower than"
                if k_val < 0.98
                else "about as fast as"
            )
            text += (
                f", which is {pace} these speeds assume: the method matches it at "
                f"a speed factor of {f.show(k)} on its own distance and climb"
            )
        out.append(_say(text + ".", om, k))
    if any("part of the fit" in n for n in report["uncertainty_notes"]):
        out.append(
            _caveat(
                "This activity is itself one of the activities the speeds were "
                "fitted on, so the comparison is not an independent check."
            )
        )
    return f, tuple(s for s in out if s.text)


def _prov(a: dict[str, Any]) -> Any:
    return {"preset": "reference", "user": "label", "fitted": "fitted"}[a["source"]]


# --- elevation check ---------------------------------------------------------------


def elevation(report: dict[str, Any]) -> Draft:
    f = FactSet()
    out: list[Statement] = []
    series, c = report["elevation_series"], report["comparison"]
    name = f.add("dem.name", "elevation model", series["dataset_name"], "text", "label")
    f.add("dem.kind", "model type", series["surface"], "text", "label")
    ar = f.add(
        "ascent.recorded",
        "ascent from your recording",
        c["ascent_recorded_m"],
        "m",
        "calculated",
    )
    ad = f.add(
        "ascent.model",
        "ascent from the elevation model",
        c["ascent_dem_m"],
        "m",
        "calculated",
    )
    if ar and ad:
        diff = float(f[ad].value) - float(f[ar].value)
        dd = f.add(
            "ascent.difference",
            "model minus recorded ascent",
            abs(diff),
            "m",
            "calculated",
        )
        assert dd
        side = "more" if diff > 0 else "less"
        text = (
            f"Your recording gives {f.show(ar)} of ascent, the "
            f"{series['dataset_name']} model {f.show(ad)}: {f.show(dd)} {side}."
        )
        out.append(_say(text, ar, ad, dd, name, "dem.kind"))
        if series["surface"] == "surface" and diff > 0:
            out.append(
                _caveat(
                    "This is a surface model, which follows treetops and roofs, "
                    "so in forest it tends to add ascent that is not on the ground."
                )
            )
    elif ar:
        out.append(
            _say(
                "The model does not cover the whole track, so it gives no ascent "
                f"to compare with your {f.show(ar)}.",
                ar,
            )
        )
    med = f.add(
        "difference.median",
        "typical offset (recorded minus model)",
        c["median_difference_m"],
        "m",
        "calculated",
    )
    sd = f.add(
        "difference.spread",
        "spread of the difference",
        c["std_difference_m"],
        "m",
        "calculated",
    )
    if med and sd:
        out.append(
            _say(
                f"Along the track your recorded height differs from the model by "
                f"{f.show(med)} on median, with a spread of {f.show(sd)}.",
                med,
                sd,
            )
        )
    drift = c.get("drift")
    if drift:
        rng = f.add("drift.range", "drift range", drift["range_m"], "m", "calculated")
        st = f.add(
            "drift.start",
            "difference at the start",
            drift["start_m"],
            "m",
            "calculated",
        )
        en = f.add(
            "drift.end", "difference at the end", drift["end_m"], "m", "calculated"
        )
        assert rng and st and en
        warned = any(w["code"] == "ELEVATION_DRIFT" for w in report["warnings"])
        if warned:
            out.append(
                _say(
                    f"The difference changes along the way: smoothed over a few "
                    f"hundred metres it moves from {f.show(st)} at the start to "
                    f"{f.show(en)} at the end, a range of {f.show(rng)}.",
                    st,
                    en,
                    rng,
                )
            )
            out.append(
                _caveat(
                    "That pattern looks like drift of the recorded (barometric) "
                    "height, so ascent from the recording is affected."
                )
            )
        else:
            out.append(
                _say(
                    f"The smoothed difference stays within a range of "
                    f"{f.show(rng)}, so there is no sign of drift.",
                    rng,
                )
            )
    slope = report.get("terrain_slope")
    if slope and slope["median_deg"] is not None:
        sm = f.add(
            "slope.median",
            "median terrain slope",
            slope["median_deg"],
            "deg",
            "calculated",
        )
        s90 = f.add(
            "slope.p90",
            "terrain slope that 90 % of the distance stays below",
            slope["p90_deg"],
            "deg",
            "calculated",
        )
        sx = f.add(
            "slope.max", "steepest terrain slope", slope["max_deg"], "deg", "calculated"
        )
        assert sm and s90 and sx
        out.append(
            _say(
                f"The ground around the track has a median slope of {f.show(sm)}; "
                f"90 % of the distance is below {f.show(s90)}, and the steepest "
                f"point reaches {f.show(sx)}.",
                sm,
                s90,
                sx,
            )
        )
        out.append(
            _caveat(
                "Terrain slope describes the ground beside the path, not the "
                "steepness of the path itself, and it is not a hazard rating."
            )
        )
    return f, tuple(out)


# --- baseline ---------------------------------------------------------------------


def baseline(report: dict[str, Any]) -> Draft:
    f = FactSet()
    out: list[Statement] = []
    one, two = report["models"]["multiplier"], report["models"]["split"]
    eff = report["effort"]
    na = f.add(
        "activities", "activities used", report["n_activities"], "count", "label"
    )
    nr = f.add("routes", "distinct routes", report["n_routes"], "count", "label")
    f.add("effort.fitted_on", "efforts fitted", eff["fitted_on"], "text", "label")
    assert na is not None and nr is not None
    out.append(
        _say(
            f"Your personal speeds are fitted on {f.show(na)} activities from "
            f"{f.show(nr)} different routes with {eff['fitted_on']} effort.",
            na,
            nr,
            "effort.fitted_on",
        )
    )
    if report.get("synthetic_data"):
        out.append(_caveat(SYNTHETIC_CAVEAT))
    need1 = f.add(
        "model1.min_routes", "routes model 1 needs", one["min_routes"], "count", "label"
    )
    if one["speed_multiplier"] is None:
        out.append(
            _say(
                f"That is not enough yet for even the simplest model, which "
                f"needs {f.show(need1 or '')} routes.",
                need1,
            )
        )
    else:
        k = f.add(
            "model1.factor",
            "your speed factor against the DIN standard",
            one["speed_multiplier"],
            "factor",
            "fitted",
        )
        lo, hi = one["single_activity_interval_95"]
        klo = f.add(
            "model1.single.low", "lower end for one new hike", lo, "factor", "fitted"
        )
        khi = f.add(
            "model1.single.high", "upper end for one new hike", hi, "factor", "fitted"
        )
        e1 = f.add(
            "model1.error",
            "typical error with one route left out",
            one["validation"]["median_abs_error"],
            "percent",
            "fitted",
        )
        assert k and klo and khi and e1
        out.append(
            _say(
                f"Model 1 gives you a speed factor of {f.show(k)} against the DIN "
                "standard hiker (x1.00 would be exactly average). "
                f"A single new hike may land anywhere between {f.show(klo)} and "
                f"{f.show(khi)}; with one route left out, the predicted moving "
                f"time was typically off by {f.show(e1)}.",
                k,
                klo,
                khi,
                e1,
            )
        )
    need2 = f.add(
        "model2.min_routes", "routes model 2 needs", two["min_routes"], "count", "label"
    )
    if two["horizontal_factor"] is None:
        out.append(
            _say(
                f"Model 2, which separates flat speed from climbing speed, is not "
                f"fitted yet: {two['reason']}.",
                need2,
            )
        )
    else:
        kh = f.add(
            "model2.flat",
            "flat speed factor",
            two["horizontal_factor"],
            "factor",
            "fitted",
        )
        kv = f.add(
            "model2.vertical",
            "climbing speed factor",
            two["vertical_factor"],
            "factor",
            "fitted",
        )
        e2 = f.add(
            "model2.error",
            "typical error with one route left out",
            two["validation"]["median_abs_error"],
            "percent",
            "fitted",
        )
        assert kh and kv and e2
        out.append(
            _say(
                f"Model 2 separates the two: {f.show(kh)} on the flat and "
                f"{f.show(kv)} when climbing, with a typical error of {f.show(e2)}.",
                kh,
                kv,
                e2,
            )
        )
    for i, g in enumerate(eff["observed_by_effort"]):
        gk = f.add(
            f"effort.{i}.factor",
            f"observed speed factor, {g['effort']} effort",
            g["mean_implied_multiplier"],
            "factor",
            "observed",
        )
        gn = f.add(
            f"effort.{i}.count",
            f"activities with {g['effort']} effort",
            g["n_activities"],
            "count",
            "observed",
        )
        assert gk and gn
        who = (
            "Activities without an effort note"
            if g["effort"] == "not stated"
            else f"Activities you marked as {g['effort']}"
        )
        out.append(
            _say(
                f"{who} ({f.show(gn)}) averaged a speed factor of {f.show(gk)}.",
                gk,
                gn,
            )
        )
    if eff["observed_by_effort"] and len(eff["observed_by_effort"]) > 1:
        out.append(
            _caveat(
                "These averages compare efforts on different routes, so terrain "
                "may explain part of the difference."
            )
        )
    return f, tuple(out)


TOPICS: dict[str, Callable[[dict[str, Any]], Draft]] = {
    "scenarios": scenarios,
    "elevation": elevation,
    "baseline": baseline,
}
