"""Circuit edit review and execution gates without built-in PDK data."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from cadai import circuit_edit
from cadai.circuit_spec_schema import CircuitSpecError, digest


def args(mode="rebuild", **edit):
    value = {
        "spec": {"nets": [{"name": "IN", "scope": "signal"},
                          {"name": "gnd!", "scope": "ground"}]},
        "bindings": [],
        "layout": {"routing": {"mode": "end_to_end"}},
        "task_ref": "task:edit",
        "request_id": "edit_one",
        "geometry_plan_digest": "a" * 64,
        "geometry": {},
        "preview_digest": "p" * 64,
        "target_ref": "target:edit",
        "target_action": "edit",
        "edit": {"mode": mode, **edit},
    }
    return value


class Client:
    def __init__(self, replies):
        self.calls = []
        self.replies = list(replies)

    def call(self, method, inputs):
        self.calls.append((method, inputs["code"]))
        return self.replies.pop(0)


def plan():
    return ["lib", "cell", "view"], [("inst",), ("other",)], [("port",)], [
        ("wire", "a", "b", True), ("wire", "c", "d")], ["note"]


def test_edit_plan_modes_include_reviewed_deletions():
    assert circuit_edit.edit_plan(args(), "digest")[0] == ["mode", "rebuild"]
    plan = circuit_edit.edit_plan(args(
        "patch", delete={"instances": ["I"], "wires": True}), "digest")
    assert plan[-1][1][0] == ["instances", ["I"]]
    reroute = circuit_edit.edit_plan(args("reroute", nets=["IN"]), "digest")
    assert ["nets", ["IN"]] in reroute[-1][1]
    with pytest.raises(CircuitSpecError):
        circuit_edit.edit_plan(args("reroute"), "digest")
    with pytest.raises(CircuitSpecError):
        circuit_edit.edit_plan(args("patch"), "digest")


def test_review_and_question_describe_each_mode(monkeypatch):
    monkeypatch.setattr(circuit_edit, "payload", lambda *_args, **_kwargs: plan())
    monkeypatch.setattr(circuit_edit, "preview_circuit",
                        lambda *_args: {"plan": {"spec": {"nets": [
                            {"name": "IN"}, {"name": "OUT"}]}}})
    body, review_digest = circuit_edit.review(args("rebuild"))
    assert body["target"] == ["lib", "cell", "view"]
    assert body["wires"] == {"drawn": 1, "connections": 1}
    assert body["notes"] == 1 and body["spec_nets"] == ["IN", "OUT"]
    assert review_digest == digest(body)
    for mode in ("reroute", "patch"):
        body, _ = circuit_edit.review(args(mode, nets=["IN"],
                                           delete={"instances": ["I"]}))
        assert body["mode"] == mode
    question = circuit_edit.question(body, review_digest)
    assert question["id"] == "circuit_edit"
    assert review_digest[:12] in question["question"]


def test_edit_values_validates_target_mode_stub_and_ground(monkeypatch):
    monkeypatch.setattr(circuit_edit, "payload",
                        lambda *_args, **_kwargs: ["payload"])
    missing_target = args()
    missing_target.pop("target_ref")
    with pytest.raises(CircuitSpecError, match="together"):
        circuit_edit.edit_values(missing_target, None)
    with pytest.raises(CircuitSpecError, match="target_action"):
        circuit_edit.edit_values({**args(), "target_action": "open"}, None)
    with pytest.raises(CircuitSpecError, match="Unsupported"):
        circuit_edit.edit_values(args("other"), None)
    with pytest.raises(CircuitSpecError, match="stub"):
        circuit_edit.edit_values({**args(), "layout": {
            "routing": {"mode": "stub"}}}, None)
    with pytest.raises(CircuitSpecError, match="Ground"):
        circuit_edit.edit_values(args("reroute", nets=["gnd!"]), None)
    assert circuit_edit.edit_values(args(), None) == [
        "task:edit", "edit_one", "a" * 64, ["payload"]]


def test_execute_edit_handles_guard_retry_and_transport_failure(monkeypatch):
    monkeypatch.setattr(circuit_edit, "validate", lambda *_args: None)
    monkeypatch.setattr("cadai.circuit_preview.attach_svg_preview",
                        lambda result, *_args: result)
    service = SimpleNamespace(validate_execute=lambda guard: None)
    monkeypatch.setattr(circuit_edit, "require_service", lambda _bindings: service)
    success = {"value": json.dumps({"ok": True, "saved": True,
                                    "user_approved": True})}
    guard = {"value": json.dumps({"ok": True, "code": "pdk_validation_required",
                                  "pdk_guard": "guard"})}
    client = Client([(True, guard), (True, success)])
    request = {"prepare_ref": "prepare", "geometry_plan_digest": "a" * 64,
               "request_id": "edit", "approval_digest": "b" * 64}
    ok, result = circuit_edit.execute_edit(request, client)
    assert ok and result["user_approved"] is True
    assert len(client.calls) == 2 and "guard" in client.calls[-1][1]
    client = Client([(False, {"code": "transport"})])
    ok, result = circuit_edit.execute_edit(request, client)
    assert ok is False and result["code"] == "transport"


def test_call_edit_rejects_stale_bindings_and_missing_inputs(monkeypatch):
    monkeypatch.setattr(circuit_edit, "validate", lambda *_args: None)
    value = args()
    value["binding_refs"] = [{"id": "b"}]
    with pytest.raises(CircuitSpecError, match="expired"):
        circuit_edit.call_edit("prepare_circuit_edit", value, Client([]))
    with pytest.raises(CircuitSpecError, match="missing"):
        circuit_edit.call_edit("prepare_circuit_edit",
                               {"edit": {"mode": "rebuild"}}, Client([]))
    with pytest.raises(CircuitSpecError, match="names the nets"):
        circuit_edit.call_edit("prepare_circuit_edit", args("reroute"), Client([]))


def test_call_edit_prepare_returns_review_and_execute_maps_approval(monkeypatch):
    monkeypatch.setattr(circuit_edit, "validate", lambda *_args: None)
    monkeypatch.setattr(circuit_edit, "review",
                        lambda *_args, **_kwargs: ({"mode": "rebuild"}, "r" * 64))
    monkeypatch.setattr(circuit_edit, "edit_values",
                        lambda *_args: ["task", "request", "digest", "payload"])
    monkeypatch.setattr(circuit_edit, "guards", lambda _args: None)
    monkeypatch.setattr(circuit_edit, "question", lambda *_args: {"id": "q"})
    client = Client([(True, {"value": '{"ok":true}'})])
    ok, result = circuit_edit.call_edit("prepare_circuit_edit", args(), client)
    assert ok and result["stage"] == "edit_review"
    assert result["selection_question"] == {"id": "q"}
    assert result["review_digest"] == "r" * 64

    captured = []
    monkeypatch.setattr(circuit_edit, "execute_edit",
                        lambda value, client, **kwargs: captured.append(value) or (True, {}))
    assert circuit_edit.call_edit("execute_circuit_edit", {
        "approval": {"review_digest": "d" * 64}}, None)[0] is True
    assert captured[0]["approval_digest"] == "d" * 64
