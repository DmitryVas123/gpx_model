import itertools

import pytest

from alpha.domain.climbs import ClimbSection, Direction, find_climb_sections
from alpha.domain.profile import ProfileBin

BIN = 50.0


def bins(*gradients: float | None) -> tuple[ProfileBin, ...]:
    result = []
    for k, g in enumerate(gradients):
        change = None if g is None else g / 100 * BIN
        result.append(
            ProfileBin(k, k * BIN, (k + 1) * BIN, None, None, change, g, None)
        )
    return tuple(result)


def find(
    profile: tuple[ProfileBin, ...], min_change: float = 10.0
) -> tuple[ClimbSection, ...]:
    return find_climb_sections(profile, 8.0, 100.0, min_change)


def test_single_climb() -> None:
    (c,) = find(bins(0, 10, 10, 10, 10, 0))
    assert c.direction is Direction.UP
    assert (c.start_distance_m, c.end_distance_m) == (50.0, 250.0)
    assert c.elevation_change_m == pytest.approx(20.0)
    assert c.mean_gradient_percent == pytest.approx(10.0)
    assert (c.start_bin, c.end_bin) == (1, 4)


def test_short_mild_interruption_is_bridged() -> None:
    (c,) = find(bins(10, 10, 2, 2, 10, 10))  # 100 m at 2 %
    assert (c.start_bin, c.end_bin) == (0, 5)
    assert c.max_bin_gradient_percent == 10.0


def test_long_interruption_splits() -> None:
    sections = find(bins(20, 20, 0, 0, 0, 20, 20))  # 150 m flat > 100 m
    assert [(s.start_bin, s.end_bin) for s in sections] == [(0, 1), (5, 6)]


def test_opposite_steep_bin_splits() -> None:
    sections = find(bins(20, 20, -10, 20, 20), min_change=15.0)
    assert [(s.direction, s.start_bin, s.end_bin) for s in sections] == [
        (Direction.UP, 0, 1),
        (Direction.UP, 3, 4),
    ]


def test_unknown_gradient_splits() -> None:
    sections = find(bins(20, 20, None, 20, 20), min_change=15.0)
    assert len(sections) == 2


def test_small_climb_is_dropped() -> None:
    assert find(bins(10, 10), min_change=50.0) == ()


def test_descent_and_ordering() -> None:
    sections = find(bins(-20, -20, 0, 0, 0, 20, 20))
    assert [s.direction for s in sections] == [Direction.DOWN, Direction.UP]
    down = sections[0]
    assert down.elevation_change_m == pytest.approx(20.0)
    assert down.mean_gradient_percent == pytest.approx(-20.0)
    assert down.max_bin_gradient_percent == -20.0


def test_net_change_counts_mild_interruptions() -> None:
    # Steep bins gain 8 m in total, mild descents in between lose 7 m.
    (c,) = find(bins(8, -7, 8), min_change=0.5)
    assert c.elevation_change_m == pytest.approx(4.5)
    # With a long allowed interruption the net change can turn negative:
    # +4 -3.5 -3.5 -3.5 +4 = -2.5 m, which is not a climb.
    assert find_climb_sections(bins(8, -7, -7, -7, 8), 8.0, 200.0, 0.0) == ()


def test_up_and_down_sections_never_overlap() -> None:
    profile = bins(30, 30, -40, -40, 30, 30, -40, -40)
    sections = find(profile, min_change=1.0)
    spans = sorted((s.start_bin, s.end_bin) for s in sections)
    for (_, end), (start, _) in itertools.pairwise(spans):
        assert end < start
