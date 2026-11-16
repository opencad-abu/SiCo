"""Approved in-place circuit edit: review gate, approval digest and native shape."""

from __future__ import annotations

import json

import pytest
from circuit_geometry_fixture import sample_geometry

from cadai import mcp
from cadai.circuit_edit import EDIT_NAMES, OPERATION, call_edit, question, review
from cadai.circuit_geometry import preview_geometry
from cadai.circuit_spec_plan import preview_circuit
from cadai.circuit_spec_schema import CircuitSpecError, digest


def _args():
    args = sample_geometry()
    args["spec"]["ports"] = [
        {"name": name, "net": name, "direction": direction}
        for name, direction in (
            ("gnd!", "inputOutput"), ("IN", "input"), ("OUT", "output"),
        )
    ]
    args["layout"]["ports"] = {
        "gnd!": {"xy": [-6, 1], "escape": "right"},
        "IN": {"xy": [-6, -1], "escape": "right"},
        "OUT": {"xy": [6, 0], "escape": "left"},
    }
    args["layout"]["routing"] = {
        "mode": "end_to_end",
        "decision": {"source": "user", "chosen": "end_to_end"},
    }
    args["preview_digest"] = preview_circuit(args["spec"], args["bindings"])["preview_digest"]
    args["geometry_plan_digest"] = preview_geometry(**args)["geometry_plan_digest"]
    args["target_ref"] = "circuit-target:observed"
    args["target_action"] = "edit"
    args["edit"] = {"mode": "rebuild"}
    args["task_ref"] = "task:edit"
    args["request_id"] = "edit_one"
    return args


class Client:
    def __init__(self, replies=None):
        self.calls = []
        self.replies = list(replies or [])

    def call(self, method, inputs):
        assert method == "eval_skill_native"
        self.calls.append(inputs["code"])
        if self.replies:
            return self.replies.pop(0)
        return True, {"value": '{"ok":true,"prepare_ref":"create-prepare:edit_one"}'}


def test_edit_tools_are_registered_everywhere():
    names = {entry["name"] for entry in mcp.TOOLS}
    assert EDIT_NAMES <= names
    from cadai.pdk_preparation import DESIGN_TOOLS
    assert EDIT_NAMES <= DESIGN_TOOLS
    from sico.adapters.circuit import NAMES as AGENT_NAMES
    assert EDIT_NAMES <= AGENT_NAMES
    assert OPERATION == "edit_circuit"


def test_prepare_returns_a_review_and_a_user_question():
    client = Client()
    ok, result = call_edit("prepare_circuit_edit", _args(), client)
    assert ok and result["ok"]
    assert result["stage"] == "edit_review"
    assert result["user_input_required"] is True
    assert result["next_action"] == "ask_user_to_approve_circuit_edit"
    assert result["review"]["mode"] == "rebuild"
    assert result["review"]["instances"] and result["review"]["ports"]
    assert result["review"]["wires"]["connections"] >= 1
    assert result["review_digest"] == digest(result["review"])
    assert result["selection_question"]["id"] == "circuit_edit"
    code = client.calls[0]
    assert code.startswith("aiCreatePrepare(")
    assert 'list("circuit-target:observed" "edit")' in code
    assert '"rebuild"' in code and result["review_digest"] in code
    assert "prepare_ref" in result


def test_review_describes_what_the_write_will_clear_and_draw():
    args = _args()
    body, review_digest = review(args)
    assert body["target"] == ["work_a", "my_tb", "schematic"]
    assert "every instance" in body["clears"]
    assert sorted(body["instances"]) == ["I_filter_0", "Rout", "Vin"]
    assert sorted(body["ports"]) == ["IN", "OUT", "gnd!"]
    assert body["wires"]["connections"] >= 1 and body["wires"]["drawn"] >= 1
    text = question(body, review_digest)["question"]
    assert "work_a/my_tb/schematic" in text and review_digest[:12] in text


def test_execution_carries_the_approval_digest_and_retries_the_guard():
    client = Client([(True, {"value": json.dumps({
        "ok": True, "code": "pdk_validation_required", "pdk_guard": "guard:one"})}),
        (True, {"value": '{"ok":true,"saved":true,"edited":true,"user_approved":true}'})])
    from cadai.circuit_edit import execute_edit

    class Service:
        def validate_execute(self, guard):
            assert guard == "guard:one"

    class Bindings:
        def require(self):
            return Service()

    args = {
        "prepare_ref": "create-prepare:edit_one",
        "geometry_plan_digest": "a" * 64,
        "request_id": "edit_one",
        "approval_digest": "b" * 64,
    }
    import cadai.circuit_edit as module

    original = module.require_service
    module.require_service = lambda _: Service()
    try:
        ok, result = execute_edit(args, client)
    finally:
        module.require_service = original
    assert ok and result["user_approved"] is True
    assert len(client.calls) == 2
    assert client.calls[0].startswith("aiCreateExecute(")
    assert client.calls[0].count('"' + "b" * 64 + '"') == 1
    assert "guard:one" in client.calls[1] and "b" * 64 in client.calls[1]


def test_patch_plan_carries_the_reviewed_delete_lists():
    args = _args()
    args["edit"] = {"mode": "patch", "delete": {"instances": ["Rout"], "nets": ["OUT"], "wires": True}}
    client = Client()
    ok, result = call_edit("prepare_circuit_edit", args, client)
    assert ok and result["review"]["mode"] == "patch"
    assert result["review"]["deletes"] == {
        "instances": ["Rout"], "ports": [], "nets": ["OUT"], "all_wires": True,
    }
    assert "only the listed objects" in result["review"]["clears"]
    code = client.calls[0]
    assert '"patch"' in code and '"Rout"' in code and '"OUT"' in code
    assert result["review_digest"] == digest(result["review"])


def test_patch_without_a_delete_list_is_rejected():
    args = _args()
    args["edit"] = {"mode": "patch"}
    with pytest.raises(CircuitSpecError):
        call_edit("prepare_circuit_edit", args, Client())


def test_reroute_plan_touches_one_net_and_keeps_the_rest():
    args = _args()
    args["edit"] = {"mode": "reroute", "nets": ["IN"]}
    client = Client()
    ok, result = call_edit("prepare_circuit_edit", args, client)
    assert ok and result["review"]["mode"] == "reroute"
    assert result["review"]["reroutes"] == ["IN"]
    assert "only the wires and labels" in result["review"]["clears"]
    code = client.calls[0]
    assert '"reroute"' in code and '"IN"' in code and '"OUT"' not in code.split("list(list(")[-1][:200]
    assert result["review_digest"] == digest(result["review"])


def test_reroute_names_nets_and_refuses_ground():
    from cadai.circuit_edit import edit_values

    args = _args()
    args["edit"] = {"mode": "reroute"}
    with pytest.raises(CircuitSpecError):
        call_edit("prepare_circuit_edit", args, Client())
    grounded = _args()
    grounded["spec"]["nets"] = [
        {"name": "gnd!", "scope": "ground"} if net["name"] == "gnd!" else net
        for net in grounded["spec"]["nets"]
    ]
    grounded["edit"] = {"mode": "reroute", "nets": ["gnd!"]}
    with pytest.raises(CircuitSpecError):
        edit_values(grounded, None)


def test_prepare_rejects_a_stub_only_rebuild():
    args = _args()
    args["layout"]["routing"] = {"mode": "stub"}
    with pytest.raises(CircuitSpecError):
        call_edit("prepare_circuit_edit", args, Client())


def test_prepare_expands_explicit_bindings_exactly_as_the_previews_do():
    """Previews hash the expanded retained masters; the edit must be planned on the
    same snapshot. Explicit bindings that differ from the retained record used to
    fail the preview-digest re-check even though preview_circuit_geometry accepted
    the same inputs, dead-ending the approved-continuation flow."""
    submitted = _args()
    retained = json.loads(json.dumps(submitted["bindings"]))
    retained["masters"][0]["terminals"] = list(
        reversed(retained["masters"][0]["terminals"])
    )
    # The previews rebuild the geometry digest from the expanded snapshot.
    submitted["geometry"]["binding_digest"] = digest(retained)

    class Service:
        def __init__(self):
            self.requests = []

        def resolve_request(self, args):
            self.requests.append(args)
            return {**args, "bindings": json.loads(json.dumps(retained))}

    submitted["preview_digest"] = preview_circuit(
        submitted["spec"], retained
    )["preview_digest"]
    submitted["geometry_plan_digest"] = preview_geometry(
        submitted["spec"],
        retained,
        submitted["preview_digest"],
        submitted["geometry"],
        submitted["layout"],
    )["geometry_plan_digest"]

    service = Service()
    ok, result = call_edit(
        "prepare_circuit_edit", submitted, Client(), pdk_bindings=service
    )
    assert service.requests, "retained masters must be expanded before planning"
    assert ok and result["ok"] and result["stage"] == "edit_review"
    assert result["review_digest"] == digest(result["review"])


def test_prepare_requires_an_inspected_target():
    args = {k: v for k, v in _args().items() if k not in ("target_ref", "target_action")}
    with pytest.raises(CircuitSpecError):
        call_edit("prepare_circuit_edit", args, Client())


def test_edit_operation_uses_its_reviewed_execute_wiring(tmp_path):
    """edit_circuit is the one write operation that executes through a
    differently named tool; its journal key, receipt reference and dispatch
    tool must stay aligned with the native edit execute claim."""
    from cadai.project_contract import receipt as checked_receipt
    from cadai.project_journal import OperationJournal
    from cadai.project_schema import OPERATIONS, operation_tool

    assert OPERATIONS["edit_circuit"] == "edit-execute:"
    assert operation_tool("edit_circuit") == "execute_circuit_edit"
    journal = OperationJournal(tmp_path)
    assert journal.key("edit_circuit", "edit_one") == "edit-execute:edit_one"
    record = {"operation": "edit_circuit", "request_id": "edit_one", "session": {}}
    checked = checked_receipt(
        {"ok": True, "circuit_ref": "created:edit_one", "saved": True, "check": [0, 0]},
        record,
    )
    assert checked["circuit_ref"] == "created:edit_one"
