"""Named extensions permit planning, never assert callback qualification offline."""

import copy

import pytest
from circuit_geometry_fixture import sample_geometry

from cadai.circuit_create import build_create_call, payload
from cadai.circuit_geometry import preview_geometry
from cadai.circuit_spec_plan import preview_circuit
from cadai.circuit_spec_schema import CircuitSpecError


def extension_args(callbacks):
    args = sample_geometry()
    args["bindings"]["masters"][0]["callbacks"] = callbacks
    preview = preview_circuit(args["spec"], args["bindings"])
    args["preview_digest"] = preview["preview_digest"]
    args["geometry"]["binding_digest"] = preview["plan"]["binding_digest"]
    return args, preview


def test_extension_ref_is_carried_without_claiming_native_qualification():
    args, preview = extension_args(dict(status="required", extension_ref="project:reviewed:v1"))
    assert not preview["plan"]["binding_complete"]
    assert preview["plan"]["issues"][0]["code"] == "callback_extension_not_validated"
    geo = preview_geometry(**args)
    args.update(
        geometry_plan_digest=geo["geometry_plan_digest"], task_ref="task:test", request_id="test"
    )
    before = copy.deepcopy(args)
    data = payload(args)
    assert sum(row[11] == "project:reviewed:v1" for row in data[1]) == 1
    assert any(row[11] is None for row in data[1])
    code = build_create_call("prepare_circuit_creation", args)
    assert '"project:reviewed:v1"' in code and "load(" not in code
    assert args == before


@pytest.mark.parametrize("callbacks", [dict(status="required"), dict(status="unknown")])
def test_unresolved_callbacks_still_block_geometry(callbacks):
    args, _ = extension_args(callbacks)
    with pytest.raises(CircuitSpecError, match="binding issues"):
        preview_geometry(**args)


def test_extension_does_not_hide_other_missing_bindings():
    args, _ = extension_args(dict(status="required", extension_ref="project:reviewed:v1"))
    args["bindings"]["masters"][0]["parameters_complete"] = False
    preview = preview_circuit(args["spec"], args["bindings"])
    args["preview_digest"] = preview["preview_digest"]
    args["geometry"]["binding_digest"] = preview["plan"]["binding_digest"]
    with pytest.raises(CircuitSpecError, match="binding issues"):
        preview_geometry(**args)
