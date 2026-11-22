"""Climb and descent sections from profile bins (DATA_MODEL.md §5.5)."""

from dataclasses import dataclass
from enum import StrEnum

from alpha.domain.profile import ProfileBin


class Direction(StrEnum):
    UP = "up"
    DOWN = "down"


@dataclass(frozen=True)
class ClimbSection:
    direction: Direction
    start_distance_m: float
    end_distance_m: float
    elevation_change_m: float  # net, in the section's direction (positive)
    mean_gradient_percent: float  # signed
    max_bin_gradient_percent: float  # signed; steepest bin in the direction
    start_bin: int
    end_bin: int  # inclusive

    @property
    def length_m(self) -> float:
        return self.end_distance_m - self.start_distance_m


def find_climb_sections(
    bins: tuple[ProfileBin, ...],
    min_gradient_percent: float,
    max_interruption_m: float,
    min_elevation_change_m: float,
) -> tuple[ClimbSection, ...]:
    """Sustained climbs and descents, ordered by start distance.

    A section is a run of bins whose gradient is at least
    ``min_gradient_percent`` in its direction. Interruptions by milder bins are
    bridged when they total at most ``max_interruption_m`` in a row. A bin
    steep in the opposite direction, or without a gradient, always ends the
    section, so up and down sections never overlap. Sections with a net
    elevation change below ``min_elevation_change_m`` are dropped.
    """
    sections = [
        *_scan(bins, Direction.UP, min_gradient_percent, max_interruption_m),
        *_scan(bins, Direction.DOWN, min_gradient_percent, max_interruption_m),
    ]
    kept = [s for s in sections if s.elevation_change_m >= min_elevation_change_m]
    return tuple(sorted(kept, key=lambda s: s.start_distance_m))


def _scan(
    bins: tuple[ProfileBin, ...],
    direction: Direction,
    min_gradient: float,
    max_interruption_m: float,
) -> list[ClimbSection]:
    sign = 1.0 if direction is Direction.UP else -1.0
    sections: list[ClimbSection] = []
    start: int | None = None
    last_steep: int | None = None
    interruption_m = 0.0

    def close() -> None:
        if start is not None and last_steep is not None:
            sections.append(_section(bins[start : last_steep + 1], direction))

    for k, b in enumerate(bins):
        g = b.gradient_percent
        steep = g is not None and sign * g >= min_gradient
        opposite = g is not None and -sign * g >= min_gradient
        if steep:
            if start is None:
                start = k
            last_steep = k
            interruption_m = 0.0
        elif start is not None:
            interruption_m += b.length_m
            if g is None or opposite or interruption_m > max_interruption_m:
                close()
                start = last_steep = None
                interruption_m = 0.0
    close()
    return sections


def _section(run: tuple[ProfileBin, ...], direction: Direction) -> ClimbSection:
    change = sum(b.ele_change_m or 0.0 for b in run)
    length = run[-1].end_distance_m - run[0].start_distance_m
    gradients = [b.gradient_percent for b in run if b.gradient_percent is not None]
    up = direction is Direction.UP
    steepest = max(gradients) if up else min(gradients)
    return ClimbSection(
        direction=direction,
        start_distance_m=run[0].start_distance_m,
        end_distance_m=run[-1].end_distance_m,
        # Net change in the section's own direction; can be negative if mild
        # interruptions outweigh the steep bins, and is then filtered out.
        elevation_change_m=change if up else -change,
        mean_gradient_percent=change / length * 100.0,
        max_bin_gradient_percent=steepest,
        start_bin=run[0].index,
        end_bin=run[-1].index,
    )
