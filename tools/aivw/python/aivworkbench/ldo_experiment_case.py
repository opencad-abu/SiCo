"""Immutable public LDO experiment case schema."""

from __future__ import annotations

from dataclasses import dataclass


import math


from typing import Mapping, Sequence

from .ldo_experiment_errors import LDOExperimentSafetyError
from .ldo_experiment_values import SAFE_ID, freeze, thaw

CASE_FIELDS = {
    "id",
    "analysis",
    "purpose",
    "dataset",
    "stimulus",
    "observables",
    "estimated_stop_time_s",
}


@dataclass(frozen=True)
class LDOExperimentCase:
    """One public, bounded calibration experiment."""

    case_id: str
    analysis: str
    purpose: str
    stimulus: Mapping[str, object]
    observables: tuple[str, ...]
    estimated_stop_time_s: float
    dataset: str = "calibration"

    def __post_init__(self) -> None:
        object.__setattr__(self, "stimulus", freeze(self.stimulus))
        object.__setattr__(self, "observables", tuple(self.observables))

    @property
    def id(self) -> str:
        return self.case_id

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.case_id,
            "analysis": self.analysis,
            "purpose": self.purpose,
            "dataset": self.dataset,
            "stimulus": thaw(self.stimulus),
            "observables": list(self.observables),
            "estimated_stop_time_s": self.estimated_stop_time_s,
        }


def case_from_mapping(value: object) -> LDOExperimentCase:
    if not isinstance(value, Mapping):
        raise LDOExperimentSafetyError("experiment case must be an object")
    unknown = set(value) - CASE_FIELDS
    missing = CASE_FIELDS - set(value)
    if unknown or missing:
        raise LDOExperimentSafetyError(
            "experiment case fields mismatch: missing=%s unknown=%s"
            % (sorted(missing), sorted(str(item) for item in unknown))
        )
    case_id = value.get("id")
    analysis = value.get("analysis")
    purpose = value.get("purpose")
    dataset = value.get("dataset")
    if not isinstance(case_id, str) or not SAFE_ID.fullmatch(case_id):
        raise LDOExperimentSafetyError("experiment case id is invalid")
    if analysis not in {"dc", "transient"}:
        raise LDOExperimentSafetyError("experiment case analysis is unsupported")
    if not isinstance(purpose, str) or not SAFE_ID.fullmatch(purpose):
        raise LDOExperimentSafetyError("experiment case purpose is invalid")
    if dataset != "calibration":
        raise LDOExperimentSafetyError(
            "public experiment cases must belong to the calibration dataset"
        )
    stimulus = value.get("stimulus")
    if not isinstance(stimulus, Mapping):
        raise LDOExperimentSafetyError("experiment case stimulus must be an object")
    raw_observables = value.get("observables")
    if (
        not isinstance(raw_observables, Sequence)
        or isinstance(raw_observables, (str, bytes))
    ):
        raise LDOExperimentSafetyError("experiment case observables must be an array")
    observables = tuple(str(item) for item in raw_observables)
    duration = value.get("estimated_stop_time_s")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)):
        raise LDOExperimentSafetyError("experiment case duration must be numeric")
    duration_float = float(duration)
    if not math.isfinite(duration_float) or duration_float < 0.0:
        raise LDOExperimentSafetyError("experiment case duration must be finite and non-negative")
    return LDOExperimentCase(
        case_id,
        analysis,
        purpose,
        dict(stimulus),
        observables,
        duration_float,
        dataset,
    )
