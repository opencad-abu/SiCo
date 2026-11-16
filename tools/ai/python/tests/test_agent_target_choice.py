"""Existing-design actions require a real answer bound to the target observation."""

from types import SimpleNamespace
import json

import pytest

from cadai.circuit_create import TARGET_CHOICES
from cadai.circuit_create import target_decision
from sico.adapters.circuit import CircuitClient
from sico.core.contracts import BoundContext, ModelTurn, NeedsReconcile, ToolCall, ToolResult
from sico.core.tools import Tool, ToolRegistry, no_arguments
from sico.providers.mock import ScriptedProvider
from sico.service.backend import create_backend
from sico.storage.journal import SessionJournal
from test_agent_pdk_choice import (  # noqa: F401
    pdk_backend, gateway, native_codex, start, tool_data, bind_input,
)
from test_circuit_create import args as inputs


@pytest.fixture
def target_backend(pdk_backend, monkeypatch):
    backend, _, _ = pdk_backend
    calls = []

    def send(client, function, values, claim=None, expected=None):
        calls.append((function, values))
        if function == "aiCrInspectTarget":
            return {"ok": True, "target_ref": "circuit-target:" + values[1],
                    "target": values[2], "content_state": "nonempty", "counts": [1, 0, 0],
                    "modified_in_session": True}
        if function == "aiTemplateSymbolBinding":
            return {"ok": True, "symbol_ref": values[0], "task_ref": "task:test",
                    "template_ref": "template:test", "preview_digest": "d" * 64,
                    "target": ["ANALOG_MACRO", "LDO_1P2", "symbol"],
                    "library_path": "/zone.disk/ssd1/workarea/projs/proj1/xh/ANALOG_MACRO",
                    "signature": ["sig"], "occupied_bbox": [[0, 0], [1, 1]],
                    "ports": [["VIN", "input", [0.0, 0.5], "left"]]}
        assert function == "aiCreatePrepare", function
        return {"ok": True, "prepare_ref": "create-prepare:test", "stage": "prepared"}

    monkeypatch.setattr(CircuitClient, "send", send)
    start(backend)
    pdk_info = tool_data(backend, "get_pdk_preparation")
    pdk_row = next(iter(backend.audit.index.audits.values()))
    backend.audit.answer(
        pdk_row["id"], {"pdk_library": {"choice": "pdk_a", "text": ""}},
        "pdk-reply", "user", pdk_row["task_id"],
    )
    backend.audit.deliver_host()
    assert backend.audit.pdk_choice("pdk_a", pdk_info, backend.state.context)
    preparation = tool_data(backend, "prepare_pdk_data", {
        "library": "pdk_a", "choice_confirmed": True,
    })
    assert preparation["status"] == "collecting"
    while preparation["status"] == "collecting":
        preparation = tool_data(backend, "advance_pdk_collection", {
            "collection_ref": preparation["collection_ref"],
            "next_batch": preparation["next_batch"],
        })
    assert preparation["status"] == "ready"
    target = inputs()["spec"]["target"]
    info = tool_data(backend, "inspect_circuit_target", {
        "task_ref": "task:test", "request_id": "observed",
        "target": target})
    assert info["target_ref"]
    assert "decision_required" not in info
    decision = tool_data(backend, "prepare_circuit_creation", inputs())
    assert decision["decision_required"] and decision["target_ref"] == info["target_ref"]
    return backend, decision, calls


def test_model_action_is_not_user_confirmation_and_answer_is_bound(target_backend):
    backend, info, calls = target_backend
    args = {**inputs(), "target_ref": info["target_ref"], "target_action": "replace"}
    row = next(r for r in backend.audit.index.audits.values() if r.get("circuit_target_ref"))
    assert row["status"] == "pending"
    blocked = backend._execute("prepare_circuit_creation", args)
    assert blocked["isError"] and json.loads(blocked["content"][0]["text"])["status"] == "waiting_user"
    assert all(name == "aiCrInspectTarget" for name, _ in calls)
    backend.audit.answer(row["id"], {"circuit_target": {"choice": TARGET_CHOICES["continue"], "text": ""}},
                         "reply", "user", row["task_id"])
    sent = []
    backend.runtime.rpc.send = lambda message, **kwargs: sent.append(message)
    assert not backend.audit.target_choice("continue", info, backend.state.context)
    bind_input(backend, row)
    backend.audit.deliver()
    assert sent and backend.audit.target_choice("continue", info, backend.state.context)
    assert not backend.audit.target_choice("replace", info, backend.state.context)
    # Unchanged contents keep the answer even when a later inspection reports
    # them under another retained ref; changed contents need a new decision.
    assert backend.audit.target_choice("continue", {**info, "target_ref": "changed"},
                                       backend.state.context)
    assert not backend.audit.target_choice("continue", {**info, "counts": [2, 0, 0]},
                                           backend.state.context)
    assert tool_data(backend, "prepare_circuit_creation", args)["decision_required"]
    assert tool_data(backend, "prepare_circuit_creation", {**args, "target_action": "continue"})["stage"] == "prepared"
    assert calls[-1][0] == "aiCreatePrepare"
    backend.audit.index.data[row["evidence"][0]["id"]]["digest"] = "changed"
    with pytest.raises(NeedsReconcile, match="Target selection evidence changed"):
        backend.audit.target_choice("continue", info, backend.state.context)


def test_repeat_inspection_of_unchanged_contents_reuses_the_answer(target_backend):
    backend, info, calls = target_backend
    inspect = {"task_ref": "task:test", "target": inputs()["spec"]["target"]}
    row = next(r for r in backend.audit.index.audits.values() if r.get("circuit_target_ref"))
    repeated = tool_data(backend, "inspect_circuit_target", {**inspect, "request_id": "repeat"})
    assert "decision_required" not in repeated
    assert len(backend.audit.index.audits) == 2
    assert list(backend.audit.pending) == [row["id"]]
    backend.audit.answer(
        row["id"], {"circuit_target": {"choice": TARGET_CHOICES["continue"], "text": ""}},
        "reply", "user", row["task_id"],
    )
    backend.audit.deliver_host()
    assert backend.audit.settled_target_action(info, backend.state.context) == "continue"
    settled = tool_data(backend, "inspect_circuit_target", {**inspect, "request_id": "settled"})
    assert settled["target_ref"] != info["target_ref"]
    assert "decision_required" not in settled and settled["decision_reused"] is True
    assert settled["target_action_confirmed"] == "continue"
    assert settled["next_action"] == "prepare_circuit_creation"
    assert len(backend.audit.index.audits) == 2
    assert backend.audit.target_choice("continue", settled, backend.state.context)
    assert not backend.audit.target_choice("replace", settled, backend.state.context)
    args = {**inputs(), "target_ref": settled["target_ref"], "target_action": "continue"}
    assert tool_data(backend, "prepare_circuit_creation", args)["stage"] == "prepared"


def test_changed_contents_need_a_new_decision(target_backend):
    backend, info, _ = target_backend
    row = next(r for r in backend.audit.index.audits.values() if r.get("circuit_target_ref"))
    backend.audit.answer(
        row["id"], {"circuit_target": {"choice": TARGET_CHOICES["continue"], "text": ""}},
        "reply", "user", row["task_id"],
    )
    backend.audit.deliver_host()
    assert backend.audit.settled_target_action(info, backend.state.context) == "continue"
    changed = {**info, "target_ref": "circuit-target:changed", "counts": [2, 0, 0]}
    assert backend.audit.settled_target_action(changed, backend.state.context) is None
    assert not backend.audit.target_choice("continue", changed, backend.state.context)


def test_evidence_reads_stay_admitted_while_a_target_decision_is_pending(target_backend):
    backend, info, calls = target_backend
    binding = tool_data(backend, "get_symbol_binding", {
        "symbol_ref": "template-symbol:test", "master_id": "ldo1p2", "instance_id": "ldo1p2"})
    assert binding["stage"] == "symbol_binding"
    assert calls[-1][0] == "aiTemplateSymbolBinding"
    blocked = backend._execute("prepare_circuit_creation", {
        **inputs(), "target_ref": info["target_ref"], "target_action": "replace"})
    assert blocked["isError"] and json.loads(blocked["content"][0]["text"])["status"] == "waiting_user"
    assert all(name == "aiCrInspectTarget" for name, _ in calls
               if name != "aiTemplateSymbolBinding")
    assert backend.tools.resolve("inspect_schematic").effect == "read"
    assert backend.tools.resolve("get_symbol_binding").input_dependencies({}, None) == frozenset({"target"})


@pytest.mark.parametrize("fault", ["cancel", "source", "evidence"])
def test_stale_or_cancelled_decision_never_authorizes_existing_writes(target_backend, fault):
    backend, info, calls = target_backend
    row = next(iter(backend.audit.index.audits.values()))
    if row.get("pdk_selection_ref"):
        backend.audit.answer(
            row["id"], {"pdk_library": {"choice": "pdk_a", "text": ""}},
            "pdk-reply", "user", row["task_id"],
        )
        backend.audit.deliver_host()
        row = next(r for r in backend.audit.index.audits.values() if r.get("circuit_target_ref"))
    bind_input(backend, row)
    backend.audit.answer(row["id"], {"circuit_target": {"choice": TARGET_CHOICES["replace"], "text": ""}},
                         "reply", "user", row["task_id"])
    sent = []
    backend.runtime.rpc.send = lambda message, **kwargs: sent.append(message)
    if fault == "cancel":
        backend.cancelled = lambda: True
    elif fault == "source":
        backend.state.context.snapshot["changed"] = True
    else:
        backend.audit.index.data[row["evidence"][0]["id"]]["digest"] = "changed"
    backend.audit.deliver()
    assert not any("result" in frame for frame in sent)
    assert not backend.audit.target_choice("replace", info, backend.state.context)
    assert all(name == "aiCrInspectTarget" for name, _ in calls)


@pytest.mark.parametrize("outcome", ["continue", "cancel", "changed"])
def test_python_provider_waits_resumes_same_task_and_preserves_tool_group(tmp_path, outcome):
    registry = ToolRegistry()
    observed = target_decision({"ok": True, "target_ref": "circuit-target:observed",
                               "target": ["Work", "Top", "schematic"], "content_state": "nonempty"})
    dispatched = []

    def dependent(_, ctx):
        dispatched.append(registry.target_choice_validator("continue", observed, ctx))
        return ToolResult(data={"authorized": dispatched[-1]})

    for name, handler in [("inspect_circuit_target", lambda *_: ToolResult(data=observed)),
                          ("get_context", lambda *_: ToolResult(data={"valid": True})),
                          ("dependent_tool", dependent)]:
        registry.register(Tool(name, name, {"type": "object"}, no_arguments, handler))
    provider = ScriptedProvider([
        [ModelTurn("", (ToolCall("inspect", "inspect_circuit_target", {}),
                        ToolCall("next", "dependent_tool", {})))],
        [ModelTurn("")],
    ])
    context = BoundContext("instance", "generation", "bound", {"cwd": str(tmp_path)})
    with SessionJournal(tmp_path, "local_target") as journal:
        backend = create_backend(provider, registry, journal, context)
        cancelled = False
        stream = backend.run("Continue designing this schematic", lambda: cancelled)
        for event in stream:
            if event["kind"] == "model.status" and event["payload"]["phase"] == "waiting_user":
                break
        else:
            pytest.fail("Host did not wait for the user's decision")
        assert not dispatched and len(provider.requests) == 1
        row = next(iter(backend.audit.index.audits.values()))
        task_id = backend.state.task["id"]
        if outcome == "cancel":
            cancelled = True
        else:
            backend.audit.answer(row["id"], {"circuit_target": {
                "choice": TARGET_CHOICES["continue"], "text": ""}}, "reply", "user", task_id)
            if outcome == "changed":
                backend.state.context.snapshot["changed"] = True
        events = list(stream)
        assert backend.state.task["id"] == task_id
        if outcome != "continue":
            assert events[-1]["kind"] == "task." + ("cancelled" if outcome == "cancel" else "needs_reconcile")
            assert not dispatched and len(provider.requests) == 1
            return
        assert events[-1]["kind"] == "task.completed" and dispatched == [True]
        assert backend.audit.index.audits[row["id"]]["status"] == "answered"
        messages = provider.requests[-1]["messages"]
        assert [message["role"] for message in messages] == ["user", "assistant", "tool", "tool", "user"]
        assert TARGET_CHOICES["continue"] in messages[-1]["content"]
