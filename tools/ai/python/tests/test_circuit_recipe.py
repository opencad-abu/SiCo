"""Bounded recipe contract, task mode and safe native execution integration."""
import json
import math

import pytest

from cadai.circuit_recipe import RECIPE_TOOLS, preview_rc_circuit
from cadai.circuit_tools import CircuitArgumentError, build_circuit_skill
from cadai.mcp import McpServer
from test_circuit_tools import Client, call

SPEC = {"library": "rcWork", "cell": "tb"}


def create_args(**updates):
    spec = {**SPEC, **updates}
    return {**spec, "task_ref": "task:one", "request_id": "create-1",
            "preview_digest": preview_rc_circuit(spec)["preview_digest"]}


def test_preview_finite_ramp_expectation_and_numeric_normalization():
    result = preview_rc_circuit(SPEC)
    assert result["tau_s"] == pytest.approx(1e-6)
    expected = 1 - 1000 * (math.exp(-1) - math.exp(-1.001))
    assert result["expected"]["tran_tau_v"] == pytest.approx(expected, abs=1e-12)
    assert result["expected"]["dc_out_v"] == 1
    assert result["preview_digest"] == preview_rc_circuit({**SPEC, "resistance_ohm": 1000.0})["preview_digest"]
    assert preview_rc_circuit({**SPEC, "amplitude_v": 2})["expected"]["tran_tau_v"] == pytest.approx(2*expected)
    assert result["connections"]["OUT"] == ["R1.MINUS", "C1.PLUS", "OUT.output"]


@pytest.mark.parametrize("updates", [
    {"library": "a/b"}, {"cell": "tb;exit()"}, {"cell": "tb schematic"},
    {"amplitude_v": True}, {"amplitude_v": float("nan")}, {"amplitude_v": float("inf")},
    {"resistance_ohm": -1}, {"capacitance_f": "1n"}, {"unknown": 1},
    {"resistance_ohm": 1, "capacitance_f": 1e-15},
    {"resistance_ohm": 1e9, "capacitance_f": 1e-3},
])
def test_invalid_preview(updates):
    with pytest.raises(CircuitArgumentError):
        preview_rc_circuit({**SPEC, **updates})


@pytest.mark.parametrize("args", [
    {"task_id": "one", "presentation": "auto", "choice_confirmed": True},
    {"task_id": "one", "presentation": "foreground", "choice_confirmed": False},
    {"task_id": "one", "choice_confirmed": True},
    {"task_id": "one", "presentation": "foreground"},
])
def test_choice_must_be_explicit_at_task_start(args):
    with pytest.raises(CircuitArgumentError):
        build_circuit_skill("begin_circuit_task", args)


def test_create_requires_matching_preview_and_has_no_arbitrary_code():
    args = create_args()
    code = build_circuit_skill("create_rc_circuit", args)
    assert code.startswith('aiRcCreate("task:one" "create-1" "rcWork" "tb" 1000.0')
    with pytest.raises(CircuitArgumentError, match="preview_digest"):
        build_circuit_skill("create_rc_circuit", {**args, "amplitude_v": 2})
    with pytest.raises(CircuitArgumentError):
        build_circuit_skill("create_rc_circuit", {**args, "callback": "exit()"})
    assert build_circuit_skill("read_rc_results", {"run_ref": "rc-run:1"}) == 'aiRcResults("rc-run:1")'


def test_mcp_preview_is_local_and_live_recipe_uses_native_port():
    client = Client()
    server = McpServer(client)
    response = call(server, "preview_rc_circuit", SPEC)
    assert response["result"]["isError"] is False and not client.calls
    for name, args in [
        ("begin_circuit_task", {"task_id": "one", "presentation": "foreground", "choice_confirmed": True}),
        ("create_rc_circuit", create_args()),
        ("create_rc_maestro", {"circuit_ref": "rc:1", "request_id": "mae1"}),
        ("run_rc_simulation", {"circuit_ref": "rc:1", "request_id": "run1"}),
    ]:
        response = call(server, name, args)
        assert response["result"]["isError"] is False
        assert client.calls[-1] == ("eval_skill_native", {"code": build_circuit_skill(name, args)})
    for definition in RECIPE_TOOLS:
        assert call(McpServer(Client(), profile="verification"), definition["name"], {})["error"]["code"] == -32602


def test_existing_open_tools_reject_auto_or_missing_mode():
    target = {**SPEC, "view": "schematic", "oa_view_type": "schematic"}
    for mode in (None, "auto"):
        args = dict(target)
        if mode:
            args["presentation"] = mode
        with pytest.raises(CircuitArgumentError):
            build_circuit_skill("preflight_circuit_flow", args)
