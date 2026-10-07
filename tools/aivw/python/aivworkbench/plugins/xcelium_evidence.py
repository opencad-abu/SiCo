"""Registered comparator Xcelium evidence adapter.

This compatibility API composes domain operations without shared mixin state.
Keep historical imports until supported clients migrate to the domain owners;
remove compatibility aliases only in a future incompatible API version.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from . import xcelium_evidence_output as output
from . import xcelium_evidence_prepare as preparation
from . import xcelium_evidence_render as rendering
from . import xcelium_evidence_validation as validation
from .xcelium_evidence_contract import (
    ADAPTER_NAME, ADAPTER_VERSION, WAVEFORM_SCOPE, EvidenceHooks, EvidencePreparation,
)
from .xcelium_evidence_routes import (
    QUALIFIED_MODEL_PARAMETERS as QUALIFIED_MODEL_PARAMETERS,
    _SAFE_ID as _SAFE_ID,
    qualified_model_parameter_mapping,
    qualified_model_parameter_routes,
)
from .xcelium_evidence_prepare import _SAFE_TCL_PATH as _SAFE_TCL_PATH


class ComparatorXceliumEvidenceAdapter:
    """Model-class adapter implementing the executor's evidence protocol."""

    name = ADAPTER_NAME
    version = ADAPTER_VERSION
    waveform_scope = WAVEFORM_SCOPE

    def binding_for_plan(self, plan: Mapping[str, Any]) -> dict[str, dict[str, str]]:
        return validation.binding_for_plan(plan)

    def validate_plan(self, plan: Mapping[str, Any], binding: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
        return validation.validate_plan(plan, binding, adapter_name=self.name)

    def render_hooks(self, plan: Mapping[str, Any]) -> EvidenceHooks:
        return rendering.render_hooks(plan, waveform_scope=self.waveform_scope)

    def prepare_simulation(self, payload_root: Path, smoke_root: Path, plan: Mapping[str, Any], binding: Mapping[str, Mapping[str, Any]]) -> EvidencePreparation:
        return preparation.prepare_simulation(payload_root, smoke_root, plan, binding, adapter_name=self.name, adapter_version=self.version)

    def preflight_tools(self, tools: Mapping[str, str], plan: Mapping[str, Any]) -> dict[str, Any] | None:
        return preparation.preflight_tools(tools, plan)

    def preflight_outputs(self, payload_root: Path, plan: Mapping[str, Any]) -> list[dict[str, Any]]:
        return output.preflight_outputs(payload_root, plan)

    def verify_outputs(self, payload_root: Path, plan: Mapping[str, Any], evidence: Mapping[str, Any]) -> list[dict[str, Any]]:
        return output.verify_outputs(payload_root, plan, evidence, adapter_name=self.name)

    def _verify_model_parameter_routes(self, plan: Mapping[str, Any], evidence: Mapping[str, Any]) -> list[dict[str, Any]]:
        return output.verify_model_parameter_routes(plan, evidence, adapter_name=self.name)

    # Compatibility aliases only; domain owners do not import the facade.
    _validate_binding_item = staticmethod(validation.validate_binding_item)
    _point_names = staticmethod(validation.point_names)
    _sample_line = staticmethod(rendering.sample_line)
    _sv_string = staticmethod(rendering.sv_string)
    _tcl_path = staticmethod(preparation.tcl_path)
    _matrix_execution_scope = staticmethod(preparation.matrix_execution_scope)


_ADAPTERS: dict[str, ComparatorXceliumEvidenceAdapter] = {
    ADAPTER_NAME: ComparatorXceliumEvidenceAdapter(),
}


def get_xcelium_evidence_adapter(name: object) -> ComparatorXceliumEvidenceAdapter | None:
    if not isinstance(name, str):
        return None
    return _ADAPTERS.get(name)


__all__ = [
    "ADAPTER_NAME", "ADAPTER_VERSION", "ComparatorXceliumEvidenceAdapter",
    "EvidenceHooks", "EvidencePreparation", "get_xcelium_evidence_adapter",
    "qualified_model_parameter_mapping", "qualified_model_parameter_routes",
]
