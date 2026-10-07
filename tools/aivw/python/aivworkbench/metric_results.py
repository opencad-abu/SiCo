"""Immutable deterministic metric observations and gate results."""

from __future__ import annotations
from dataclasses import dataclass
from typing import Mapping

from .metric_values import freeze_value, thaw_value


@dataclass(frozen=True)
class MetricObservation:
    name: str
    kind: str
    expected: object
    actual: object
    passed: bool
    detail: Mapping[str, object]

    def __post_init__(self) -> None:
        # Frozen dataclasses do not freeze nested values.  Normalize every
        # payload once at construction so a caller cannot mutate a recorded
        # observation through an alias retained from the input evidence.
        object.__setattr__(self, "expected", freeze_value(self.expected))
        object.__setattr__(self, "actual", freeze_value(self.actual))
        object.__setattr__(self, "detail", freeze_value(self.detail))

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "kind": self.kind,
            "expected": thaw_value(self.expected),
            "actual": thaw_value(self.actual),
            "passed": self.passed,
            "detail": thaw_value(self.detail),
        }


@dataclass(frozen=True)
class LDOEvaluation:
    status: str
    code: str
    observations: tuple[MetricObservation, ...]
    summary: Mapping[str, object]

    def __post_init__(self) -> None:
        observations = tuple(self.observations)
        if any(not isinstance(item, MetricObservation) for item in observations):
            raise TypeError("observations must contain MetricObservation values")
        object.__setattr__(self, "observations", observations)
        object.__setattr__(self, "summary", freeze_value(self.summary))

    @property
    def passed(self) -> bool:
        return self.status == "PASS"

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "code": self.code,
            "passed": self.passed,
            "observations": [item.to_dict() for item in self.observations],
            "summary": thaw_value(self.summary),
        }

