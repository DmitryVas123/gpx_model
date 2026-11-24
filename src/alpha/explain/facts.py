"""Facts: the named, formatted values an explanation may use (ADR-020).

A fact is one value taken from an ALPHA JSON report (the same JSON the CLI
and API emit), with a stable key, a unit, and its provenance. Explanations,
from templates or a language model, may state only these values. The
validator checks every number against them.
"""

import math
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Literal

Unit = Literal[
    "duration", "km", "m", "m/h", "km/h", "factor", "percent", "deg", "count", "text"
]
Provenance = Literal[
    "calculated", "estimated", "observed", "fitted", "reference", "label"
]


@dataclass(frozen=True)
class Fact:
    key: str  # stable, dotted: "estimate.baseline.moving"
    label: str  # what it is, in words
    value: float | str
    unit: Unit
    provenance: Provenance

    def display(self) -> str:
        v = self.value
        if isinstance(v, str):
            return v
        match self.unit:
            case "duration":
                minutes = round(v / 60)
                return f"{minutes // 60}:{minutes % 60:02d} h"
            case "km":
                return f"{v / 1000:.2f} km"
            case "m":
                return f"{_no_negative_zero(v):.0f} m"
            case "m/h":
                return f"{v:.0f} m/h"
            case "km/h":
                return f"{v / 1000:.1f} km/h"
            case "factor":
                return f"x{v:.2f}"
            case "percent":
                return f"{v * 100:.0f} %"
            case "deg":
                return f"{v:.0f} deg"
            case "count":
                return f"{v:.0f}"
            case _:
                return str(v)

    def numbers(self) -> tuple[tuple[float, float], ...]:
        """Numeric forms a correct statement may use for this fact, each with
        its tolerance: half a unit of the precision it is naturally written
        with (rounding is fine, a different value is not)."""
        v = self.value
        if isinstance(v, str):
            return ()
        match self.unit:
            case "duration":
                return ((v / 60, 0.5),)  # minutes; durations are matched separately
            case "km":
                return ((v / 1000, 0.05), (v, 0.5))  # 8.33 km or 8.3 km; 8331 m
            case "m" | "m/h" | "deg" | "count":
                return ((v, 0.5),)
            case "km/h":
                return ((v / 1000, 0.05),)
            case "factor":  # x1.46, also "46 % faster" or "32 % less time"
                slower = abs(1 - 1 / v) * 100 if v else 0.0
                return ((v, 0.005), ((v - 1) * 100, 0.5), (slower, 0.5))
            case "percent":
                return ((v * 100, 0.5),)
            case _:
                return ((v, 0.5),)


class FactSet:
    """Facts by key, in insertion order."""

    def __init__(self) -> None:
        self._facts: dict[str, Fact] = {}

    def add(
        self,
        key: str,
        label: str,
        value: Any,
        unit: Unit,
        provenance: Provenance,
    ) -> str | None:
        """Add a fact unless the value is missing; returns the key if added."""
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return None
        if unit != "text" and not isinstance(value, int | float):
            raise TypeError(f"fact {key}: {value!r} is not numeric")
        self._facts[key] = Fact(key, label, value, unit, provenance)
        return key

    def __getitem__(self, key: str) -> Fact:
        return self._facts[key]

    def __contains__(self, key: object) -> bool:
        return key in self._facts

    def __iter__(self) -> Iterator[Fact]:
        return iter(self._facts.values())

    def __len__(self) -> int:
        return len(self._facts)

    def get(self, key: str) -> Fact | None:
        return self._facts.get(key)

    def show(self, key: str) -> str:
        return self._facts[key].display()


def _no_negative_zero(v: float) -> float:
    """-0.1 m is shown as "0 m", not "-0 m"."""
    return 0.0 if abs(v) < 0.5 else v
