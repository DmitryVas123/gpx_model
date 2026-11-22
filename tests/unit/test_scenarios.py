import dataclasses
import itertools

import numpy as np
import pytest

from alpha.domain.analysis import Analysis, TrackSummary, analyse
from alpha.domain.scenarios import (
    DIN_33466_STANDARD,
    SCENARIO_METHODS,
    AssumptionSource,
    DinAssumptions,
    ScenarioInputError,
    ScenarioInputs,
    din33466_breakdown,
    implied_speed_multiplier,
    run_scenarios,
)
from tests.helpers import line_track

# Hand-computed DIN 33466 example: 8 km, 600 m up, 400 m down, standard values.
#   horizontal = 8000 / 4000 h = 2.0 h = 7200 s
#   vertical   = 600 / 300 + 400 / 500 = 2.0 + 0.8 h = 2.8 h = 10080 s
#   moving     = max + min / 2 = 10080 + 3600 = 13680 s (3 h 48 min)
EXAMPLE = ScenarioInputs(distance_m=8000.0, ascent_m=600.0, descent_m=400.0)


def assumptions(**changes: float) -> DinAssumptions:
    return dataclasses.replace(DIN_33466_STANDARD, **changes)  # type: ignore[arg-type]


def hill_analysis(seconds: float | None = None) -> Analysis:
    track = line_track(
        5.0,
        6000.0,
        lambda d: 1000 + 400 * np.sin(np.pi * d / 6000),
        seconds_per_point=seconds,
    )
    return analyse(track)


# --- the formula ---------------------------------------------------------------------


def test_hand_computed_example() -> None:
    b = din33466_breakdown(EXAMPLE, DIN_33466_STANDARD)
    assert b.horizontal_s == pytest.approx(7200.0)
    assert b.vertical_s == pytest.approx(10080.0)
    assert b.moving_s == pytest.approx(13680.0)
    assert b.total_s == pytest.approx(13680.0)


def test_horizontal_dominates_on_flat_route() -> None:
    flat = ScenarioInputs(distance_m=12000.0, ascent_m=0.0, descent_m=0.0)
    b = din33466_breakdown(flat, DIN_33466_STANDARD)
    assert b.vertical_s == 0.0
    assert b.moving_s == pytest.approx(3 * 3600)


def test_breaks_and_delay_add_exactly() -> None:
    plain = din33466_breakdown(EXAMPLE, DIN_33466_STANDARD)
    with_breaks = din33466_breakdown(
        EXAMPLE, assumptions(break_s=1800.0, delay_s=600.0)
    )
    assert with_breaks.moving_s == plain.moving_s
    assert with_breaks.total_s == pytest.approx(plain.total_s + 2400.0)


def test_speed_multiplier_scales_moving_time_inversely() -> None:
    base = din33466_breakdown(EXAMPLE, DIN_33466_STANDARD, 1.0)
    faster = din33466_breakdown(EXAMPLE, DIN_33466_STANDARD, 2.0)
    assert faster.moving_s == pytest.approx(base.moving_s / 2)


@pytest.mark.parametrize(
    "field", ["horizontal_speed_m_h", "ascent_rate_m_h", "descent_rate_m_h"]
)
def test_slower_assumption_never_shortens(field: str) -> None:
    """Monotonicity invariant (CLAUDE.md §6), over a grid of inputs."""
    grid = itertools.product((0.0, 2000.0, 15000.0), (0.0, 300.0, 1500.0), (0.0, 800.0))
    for distance, ascent, descent in grid:
        inputs = ScenarioInputs(distance, ascent, descent)
        durations = [
            din33466_breakdown(
                inputs, assumptions(**{field: getattr(DIN_33466_STANDARD, field) * f})
            ).total_s
            for f in (2.0, 1.5, 1.0, 0.7, 0.4)  # progressively slower
        ]
        assert durations == sorted(durations)


@pytest.mark.parametrize(
    "changes",
    [
        {"horizontal_speed_m_h": 0.0},
        {"ascent_rate_m_h": -1.0},
        {"descent_rate_m_h": 0.0},
        {"break_s": -60.0},
        {"delay_s": -1.0},
    ],
)
def test_invalid_assumptions(changes: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        assumptions(**changes)


def test_invalid_multiplier() -> None:
    with pytest.raises(ValueError):
        din33466_breakdown(EXAMPLE, DIN_33466_STANDARD, 0.0)


# --- running scenarios ---------------------------------------------------------------


def test_registry_contains_din() -> None:
    assert SCENARIO_METHODS["din33466"].breakdown is din33466_breakdown


def test_run_scenarios_labels_order_and_provenance() -> None:
    a = hill_analysis()
    result = run_scenarios(a, DIN_33466_STANDARD)
    assert [s.label for s in result.scenarios] == ["faster", "baseline", "slower"]
    durations = [s.estimated_duration_s for s in result.scenarios]
    assert durations == sorted(durations)
    assert result.method == "din33466"
    assert result.assumptions is DIN_33466_STANDARD
    assert (result.algorithm_version, result.params_hash) == (
        a.algorithm_version,
        a.params_hash,
    )
    assert result.inputs.distance_m == a.summary.distance_m
    assert result.inputs.ascent_m == a.summary.ascent_m


def test_custom_multipliers_keep_given_order() -> None:
    result = run_scenarios(hill_analysis(), DIN_33466_STANDARD, (0.5, 1.0, 1.5, 2.0))
    assert [s.speed_multiplier for s in result.scenarios] == [0.5, 1.0, 1.5, 2.0]
    assert [s.label for s in result.scenarios] == ["x0.5", "baseline", "x1.5", "x2"]


def test_unknown_elevation_is_an_input_error() -> None:
    no_elevation = analyse(line_track(10.0, 2000.0))
    with pytest.raises(ScenarioInputError, match="ascent and descent are unknown"):
        run_scenarios(no_elevation, DIN_33466_STANDARD)


def test_unknown_method_and_empty_multipliers() -> None:
    with pytest.raises(ValueError, match="unknown scenario method"):
        run_scenarios(hill_analysis(), DIN_33466_STANDARD, method="magic")
    with pytest.raises(ValueError):
        run_scenarios(hill_analysis(), DIN_33466_STANDARD, ())


def test_notes_explain_assumptions() -> None:
    preset = run_scenarios(hill_analysis(), DIN_33466_STANDARD).uncertainty_notes
    assert any("published reference values" in n for n in preset)
    assert any("sensitivity to pace only" in n for n in preset)
    assert any("No breaks are included" in n for n in preset)
    own = DinAssumptions(5000.0, 450.0, 600.0, break_s=1800.0)
    user = run_scenarios(hill_analysis(), own, (1.0,)).uncertainty_notes
    assert own.source is AssumptionSource.USER
    assert not any("published reference values" in n for n in user)
    assert not any("sensitivity to pace" in n for n in user)
    assert not any("No breaks" in n for n in user)


def test_steep_terrain_note() -> None:
    steep = analyse(line_track(5.0, 2000.0, lambda d: 1000 + 0.3 * d))  # 30 %
    notes = run_scenarios(steep, DIN_33466_STANDARD).uncertainty_notes
    assert any("steeper than 25 %" in n for n in notes)
    gentle = run_scenarios(hill_analysis(), DIN_33466_STANDARD).uncertainty_notes
    assert not any("steeper than 25 %" in n for n in gentle)


# --- implied multiplier ---------------------------------------------------------------


def test_implied_multiplier_reproduces_observed_time() -> None:
    observed_s = 9000.0
    k = implied_speed_multiplier(EXAMPLE, DIN_33466_STANDARD, observed_s)
    assert k is not None
    assert din33466_breakdown(EXAMPLE, DIN_33466_STANDARD, k).moving_s == pytest.approx(
        observed_s
    )


def test_implied_multiplier_undefined_for_zero_times() -> None:
    assert implied_speed_multiplier(EXAMPLE, DIN_33466_STANDARD, 0.0) is None
    nothing = ScenarioInputs(0.0, 0.0, 0.0)
    assert implied_speed_multiplier(nothing, DIN_33466_STANDARD, 100.0) is None


def test_summary_without_descent_is_rejected() -> None:
    summary = dataclasses.replace(hill_analysis().summary, descent_m=None)
    assert isinstance(summary, TrackSummary)
    analysis = dataclasses.replace(hill_analysis(), summary=summary)
    with pytest.raises(ScenarioInputError):
        run_scenarios(analysis, DIN_33466_STANDARD)
