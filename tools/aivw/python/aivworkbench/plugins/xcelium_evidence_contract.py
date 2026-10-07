"""Value contracts and identity of the registered comparator evidence adapter."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


ADAPTER_NAME = "latched_dynamic_comparator.xcelium_evidence.v1"
ADAPTER_VERSION = "1"
WAVEFORM_SCOPE = "aivw_latched_comparator_l1_tb"


@dataclass(frozen=True)
class EvidenceHooks:
    """SystemVerilog fragments inserted by the model-class renderer."""

    declarations: str
    initial_setup: str
    initial_sample: str
    hold_sample: str
    case_samples: Mapping[str, str]
    case_event_fields: Mapping[str, Mapping[str, Any]]
    before_summary: str
    after_summary: str
    failure_action: str
    summary_fields: Mapping[str, Any]


@dataclass(frozen=True)
class EvidencePreparation:
    """Controlled Xcelium options and files prepared for one simulation."""

    compile_args: tuple[str, ...]
    simulation_args: tuple[str, ...]
    artifacts: tuple[Path, ...]
    metadata: Mapping[str, Any]

    @property
    def command_args(self) -> tuple[str, ...]:
        """Compatibility view of all controlled arguments."""
        return self.compile_args + self.simulation_args
