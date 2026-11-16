"""Circuit error boundaries, retained evidence and continued diagnostic reads."""

import io
import json
from concurrent.futures import Future
from pathlib import Path

import pytest
from test_agent_circuit import CircuitBroker
from test_agent_project import bound
from test_circuit_project import Client, execute, status

from cadai.project_journal import OperationJournal
from sico.adapters.circuit import CircuitClient
from sico.adapters.project import operation_status
from sico.codex.backend import CodexBackend
from sico.core.contracts import (
    CircuitCallError,
    ModelTurn,
    NeedsReconcile,
    TextDelta,
    ToolCall,
    ToolResult,
)
from sico.service.python_backend import PythonBackend
from sico.core.tools import Tool, ToolRegistry, no_arguments
from sico.providers.mock import ScriptedProvider
from sico.storage.journal import SessionJournal
from sico.transport.broker import Peer
from sico.transport.framing import VERSION
from sico.transport.relay import emit_diagnostic


def failure(code="simulation_project_dir_invalid", category="preflight", dispatched=False):
    return {
        "protocol": VERSION,
        "kind": "response",
        "generation": "generation",
        "id": "wire-id",
        "ok": False,
        "code": code,
        "error": "Simulation projectDir must be explicit and absolute",
        "diagnostic": {
            "code": code,
            "category": category,
            "stage": "describe",
            "function": "aiCopilotDescribe",
            "operation_dispatched": dispatched,
            "raw_error": '("error" "~/simulation")',
            "output": "CHECK_OUTPUT\n*WARNING* CHECK_WARNING\n",
            "output_truncated": False,
            "output_capture": "skill_ports",
        },
    }


@pytest.mark.parametrize("socket_path", [False, True])
@pytest.mark.parametrize(
    "code,category,dispatched,expected",
    [
        ("simulation_project_dir_invalid", "preflight", False, CircuitCallError),
        ("task_source_mismatch", "preflight", False, CircuitCallError),
        ("circuit_result_too_large", "execution", False, CircuitCallError),
        ("circuit_result_too_large", "outcome_unknown", True, NeedsReconcile),
        ("circuit_context_stale", "binding", False, NeedsReconcile),
        ("circuit_binding_changed", "binding", False, NeedsReconcile),
        ("circuit_outcome_unknown", "outcome_unknown", True, NeedsReconcile),
        ("circuit_requires_reconcile", "", False, NeedsReconcile),
    ],
)
def test_broker_classification_and_evidence(
    tmp_path, socket_path, code, category, dispatched, expected
):
    peer = object.__new__(Peer)
    peer.generation, peer.instance_id = "generation", "instance"
    peer.diagnostic, peer.operation_timeout = io.StringIO(), 1
    reply = failure(code, category, dispatched)
    with pytest.raises(expected) as caught:
        if socket_path:

            class Connection:
                def send(self, _):
                    pass

            peer.connection = Connection()
            pending = Future()
            pending.set_result(reply)
            peer._call(
                bound(tmp_path),
                "circuit_call",
                {"function": "aiCopilotDescribe"},
                "wire-id",
                pending,
            )
        else:
            peer._validate_reply(reply, "wire-id", "circuit_call", bound(tmp_path))
    data = caught.value.data
    assert data["raw_error"] == reply["diagnostic"]["raw_error"]
    for key in ("output", "output_truncated", "output_capture"):
        assert data[key] == reply["diagnostic"][key]
    assert data["function"] == "aiCopilotDescribe" and data["request_id"] == "wire-id"
    assert data["target_id"] == "bound" and data["instance_id"] == "instance"
    assert data["operation_dispatched"] is dispatched and not data["automatic_resume_allowed"]
    if socket_path:
        rows = [json.loads(line) for line in peer.diagnostic.getvalue().splitlines()]
        assert rows[-1]["diagnostic"]["raw_error"] == data["raw_error"]


@pytest.mark.parametrize("backend_name", ["loop", "codex"])
@pytest.mark.parametrize("fatal", [False, True])
def test_loop_continues_diagnostics_and_records_fatal_result(tmp_path, backend_name, fatal):
    registry, calls = ToolRegistry(), []
    data = failure()["diagnostic"]

    def fail(_args, _context):
        if fatal:
            raise NeedsReconcile("binding expired", data=data)
        raise CircuitCallError(
            "simulation_project_dir_invalid", "absolute path required", data=data
        )

    def read(_args, _context):
        calls.append("read")
        return ToolResult(data={"valid": True})

    for name, handler in [("fail", fail), ("read", read)]:
        registry.register(Tool(name, name, {"type": "object"}, no_arguments, handler))
    with SessionJournal(tmp_path, backend_name) as journal:
        if backend_name == "codex":
            backend = CodexBackend(None, registry, journal, bound(tmp_path))
            backend._begin("Diagnose", bound(tmp_path), "input", "chat")
            backend.active, backend.cancelled = True, lambda: False
            try:
                backend._execute("fail", {})
                backend._execute("read", {})
                assert backend.stale is fatal
            finally:
                backend.close()
        else:
            provider = ScriptedProvider(
                [
                    [ModelTurn("", (ToolCall("first", "fail", {}), ToolCall("next", "read", {})))],
                    [TextDelta("done"), ModelTurn("done")],
                ]
            )
            loop = PythonBackend(provider, registry, journal, bound(tmp_path))
            list(loop.run("Diagnose", input_id="input"))
            assert loop.state.task["status"] == ("needs_reconcile" if fatal else "completed")
        finished = [
            event["payload"] for event in journal.events() if event["kind"] == "tool.finished"
        ]
        result = next(row["result"] for row in finished if row["name"] == "fail")
        assert result["data"] == data or result["data"] == {
            **data,
            "code": "simulation_project_dir_invalid",
        }
        assert calls == ([] if fatal else ["read"])


def test_dispatch_error_survives_failed_recovery_and_offline_query(tmp_path):
    class BrokenClient(Client):
        def call(self, method, args):
            if args["code"].startswith("aiProjectReceipt("):
                raise NeedsReconcile(
                    "recovery source expired", data={"code": "circuit_context_stale"}
                )
            return super().call(method, args)

    client = BrokenClient()
    result = execute(client, tmp_path)
    assert result["state"] == "outcome_unknown"
    assert "reply lost" in result["dispatch_error"]["message"]
    assert result["recovery_error"]["code"] == "circuit_context_stale"
    offline = status(client, tmp_path, False)
    assert offline["dispatch_error"] == result["dispatch_error"]
    assert offline["last_observation"]["recovery_error"] == result["recovery_error"]
    assert execute(client, tmp_path)["state"] == "outcome_unknown" and client.dispatches == 1


@pytest.mark.parametrize("before_intent", [False, True])
def test_execute_failure_keeps_agent_usable_and_evidence(tmp_path, monkeypatch, before_intent):
    from test_agent_circuit_codex import BoundBroker

    from sico.adapters.virtuoso import context_tools

    broker = BoundBroker(tmp_path)
    original = broker.circuit_call

    def circuit_call(context, params):
        if params["function"] == "aiCopilotDescribe" and before_intent:
            raise CircuitCallError(
                "simulation_project_dir_invalid",
                "Absolute projectDir required",
                data={**failure()["diagnostic"], "request_id": "wire-describe"},
            )
        result = original(context, params)
        if params["function"] == "aiCreateExecute":
            broker.client.receipt.update(
                ok=False, saved=False, stage="instances", error='CDF changed w: "2u" -> "1u"'
            )
            result.update(output="*WARNING* CDF changed w\n", output_truncated=False,
                          output_capture="skill_ports")
        return result

    monkeypatch.setattr(broker, "circuit_call", circuit_call)
    requirements = dict(
        presentation="background",
        targets=[
            dict(
                library="Work", cell="Top", view="schematic",
                intent="create", oa_view_type="schematic"
            )
        ],
        libraries=[dict(library="Work", expected_path=str(tmp_path))],
        directories=[
            dict(path=str(tmp_path), purpose="project", min_free_bytes=1, min_free_inodes=1)
        ],
        model_files=[],
    )
    args = {
        "operation": "create_circuit_from_plan",
        "arguments": {
            "prepare_ref": "create-prepare:p",
            "geometry_plan_digest": "a" * 64,
            "request_id": "make",
        },
    }
    with SessionJournal(tmp_path, "execute-failure") as journal:
        backend = CodexBackend(None, context_tools(broker, journal), journal, bound(tmp_path))
        backend._begin("Execute and diagnose", bound(tmp_path), "input", "chat")
        backend.active, backend.cancelled = True, lambda: False
        try:
            assert not backend._execute("preflight_circuit_project", requirements)["isError"]
            reply = backend._execute("execute_circuit_operation", args)
            result = json.loads(reply["content"][0]["text"])
            assert reply["isError"] and not backend.stale
            operation_journal = OperationJournal(tmp_path)
            stored = operation_journal.read(operation_journal.key(args["operation"], "make"))
            if before_intent:
                assert result["status"] == "simulation_project_dir_invalid" and stored is None
                assert result["data"]["operation_request_id"] == "make"
                assert result["data"]["request_id"] == "wire-describe"
                assert result["data"]["operation_dispatched"] is False
            else:
                assert result["status"] == "operation_failed"
                assert stored["observation"]["receipt"]["stage"] == "instances"
                assert stored["response"]["output"] == "*WARNING* CDF changed w\n"
                assert result["data"]["last_response"]["output_capture"] == "skill_ports"
                assert "CDF changed" in result["summary"]
                backend._execute("execute_circuit_operation", args)
                assert sum(row["function"] == "aiCreateExecute" for row in broker.writes) == 1
            assert not backend._execute("preflight_circuit_project", requirements)["isError"]
            assert not backend.stale
        finally:
            backend.close()


@pytest.mark.parametrize("code,category", [("circuit_preflight_failed", "preflight"),
                                          ("binding_conflict", "binding")])
def test_known_rejection_has_retained_evidence_and_never_replays(tmp_path, monkeypatch, code, category):
    from cadai.mcp import _TOOLS_BY_NAME
    from cadai.project_tools import call_project

    broker = CircuitBroker(tmp_path)
    client = CircuitClient(broker, bound(tmp_path), tmp_path)
    monkeypatch.setattr(
        client,
        "prepare_operation",
        lambda *_: {
            "source": client.context.record(),
            "expected": broker.expected,
        },
    )
    original = broker.circuit_call

    def reject(context, params):
        if params["function"] == "aiCreateExecute":
            raise CircuitCallError(
                code,
                "Output exists",
                data={
                    "category": category,
                    "stage": "final_check",
                    "operation_dispatched": False,
                    "raw_error": "Output already exists",
                    "request_id": "wire-final",
                },
            )
        return original(context, params)

    monkeypatch.setattr(broker, "circuit_call", reject)
    # The native receipt API uses null, not the JSON text 'null', for no receipt.
    read = broker.read

    def missing_receipt(context, method, params):
        reply = read(context, method, params)
        if method == "project_receipt":
            reply["project"]["receipt_json"] = None
        return reply

    monkeypatch.setattr(broker, "read", missing_receipt)
    args = {
        "operation": "create_circuit_from_plan",
        "arguments": {
            "prepare_ref": "create-prepare:p",
            "geometry_plan_digest": "a" * 64,
            "request_id": "make",
        },
    }
    result = call_project("execute_circuit_operation", args, client, tmp_path, _TOOLS_BY_NAME)
    assert result["state"] == "dispatch_rejected"
    assert operation_status(result) == code
    journal = OperationJournal(tmp_path)
    stored = journal.read(journal.key(args["operation"], "make"))
    assert stored["dispatch_error"]["request_id"] == "wire-final"
    assert stored["dispatch_error"]["operation_request_id"] == "make"
    assert not result["automatic_resume_allowed"]
    monkeypatch.setattr(broker, "circuit_call", lambda *_: pytest.fail("must not replay"))
    assert (
        call_project("execute_circuit_operation", args, client, tmp_path, _TOOLS_BY_NAME)["state"]
        == "dispatch_rejected"
    )


def test_diagnostic_line_limit_keeps_identity_and_error():
    sink = io.StringIO()
    emit_diagnostic(
        sink,
        "skill.reply",
        request_id="wire-id",
        method="circuit_call",
        function="aiCopilotDescribe",
        detail="x" * 20000,
        diagnostic={**failure()["diagnostic"], "raw_error": "\x01" * 10000},
    )
    text = sink.getvalue()
    assert len(text.encode()) <= 8193
    row = json.loads(text)
    assert row["request_id"] == "wire-id" and row["function"] == "aiCopilotDescribe"
    assert row["diagnostic"]["stage"] == "describe" and row["diagnostic"]["raw_error"]
    assert row["diagnostic_truncated"]


@pytest.mark.parametrize(
    "dispatched,expected",
    [(False, CircuitCallError), (True, NeedsReconcile)],
)
def test_structured_reply_failure_keeps_its_code_and_diagnostic(
    tmp_path, dispatched, expected
):
    class Broker:
        def circuit_call(self, _context, _params):
            return {
                "id": "wire-error",
                "ok": False,
                "code": "pdk_capture_failed",
                "error": "capture unavailable",
                "diagnostic": {
                    "code": "pdk_capture_failed",
                    "category": "execution",
                    "stage": "invoke",
                    "operation_dispatched": dispatched,
                    "raw_error": "capture unavailable",
                    "automatic_resume_allowed": False,
                },
            }

    with pytest.raises(expected) as caught:
        CircuitClient(Broker(), bound(tmp_path), tmp_path).send("aiPdkCapture", [])
    assert caught.value.code == "pdk_capture_failed"
    assert caught.value.data["request_id"] == "wire-error"
    assert caught.value.data["operation_dispatched"] is dispatched
