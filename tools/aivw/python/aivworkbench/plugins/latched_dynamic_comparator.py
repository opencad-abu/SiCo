"""Latched differential-comparator model-class compatibility facade.

The facade owns candidate assembly and keeps the historical public import path
stable while contract, rendering, and matrix responsibilities live in owners.
"""

from __future__ import annotations

import json

from ..l1 import build_l1_test_plan
from .latched_dynamic_comparator_contract import (
    BaselineCandidate,
    BaselineGenerationRequest,
    _validate,
)
from .latched_dynamic_comparator_legacy import _render_legacy_smoke_testbench
from .latched_dynamic_comparator_l1 import _render_l1_testbench
from . import latched_dynamic_comparator_matrix as _matrix_owner
from . import latched_dynamic_comparator_model as _model_owner
from .xcelium_evidence import get_xcelium_evidence_adapter

_case_supply_values = _matrix_owner._case_supply_values
_finite_number = _matrix_owner._finite_number
_matrix_instances = _matrix_owner._matrix_instances
_sv_event = _matrix_owner._sv_event
_sv_string = _matrix_owner._sv_string
_render_model = _model_owner._render_model
_sv_name = _model_owner._sv_name


def _render_smoke_testbench(spec, test_plan=None, request_options=None):
    if test_plan is not None:
        return _render_l1_testbench(spec, test_plan, request_options=request_options)
    return _render_legacy_smoke_testbench(spec)


def generate_baseline_candidate(request: BaselineGenerationRequest) -> BaselineCandidate:
    _validate(request.spec, request.contract)
    module = str(request.spec["target"]["module"])
    test_plan = build_l1_test_plan(request.verification) if request.verification else None
    adapter_option_present = "xcelium_evidence_adapter" in request.model_options
    adapter_name = request.model_options.get("xcelium_evidence_adapter")
    if adapter_option_present and (
        not isinstance(adapter_name, str)
        or not adapter_name.strip()
        or adapter_name != adapter_name.strip()
    ):
        raise ValueError("xcelium_evidence_adapter must be non-empty text")
    adapter = get_xcelium_evidence_adapter(adapter_name) if adapter_option_present else None
    if adapter_option_present and adapter is None:
        raise ValueError(f"unknown xcelium evidence adapter: {adapter_name}")
    evidence_binding = None
    if adapter is not None and test_plan is not None:
        evidence_binding = adapter.binding_for_plan(test_plan)
        findings = adapter.validate_plan(test_plan, evidence_binding)
        if findings:
            raise ValueError(
                "Xcelium evidence adapter cannot qualify this L1 contract: "
                + json.dumps(findings, sort_keys=True)
            )
    return BaselineCandidate(
        source=_render_model(request),
        smoke_testbench=_render_smoke_testbench(
            request.spec, test_plan, request.model_options
        ),
        model_version="aivw-m1-rnm-baseline-1",
        module=module,
        test_plan=test_plan,
        xcelium_evidence_binding=evidence_binding,
        xcelium_evidence_adapter=adapter.name if adapter is not None else None,
    )


# Private names remain available during the compatibility window.
__all__ = [
    "BaselineCandidate",
    "BaselineGenerationRequest",
    "generate_baseline_candidate",
]
