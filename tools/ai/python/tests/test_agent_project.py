"""Shared project read contracts, transport rejection, and no-replay queue boundary."""

import json
from copy import deepcopy

import pytest
from test_circuit_project import Client, execute
from test_project_contract_boundaries import observations

from cadai.circuit_spec_schema import CircuitSpecError
from cadai.project_schema import PROJECT_TOOLS
from cadai.project_journal import OperationJournal
from sico.adapters.project import ProjectReadClient, register_project
from sico.adapters.virtuoso import context_tools
from sico.core.contracts import BoundContext, ModelTurn, TextDelta, ToolCall
from sico.service.python_backend import PythonBackend
from sico.core.tools import ToolRegistry
from sico.providers.mock import ScriptedProvider
from sico.storage.journal import SessionJournal
from sico.transport.broker import ContextBroker
from sico.transport.methods import QueryUnavailable
from sico.transport.project import PROJECT_READ_METHODS, wire_arguments


def bound(path):
    return BoundContext(
        "instance",
        "generation",
        "bound",
        {
            "cwd": str(path),
            "capabilities": sorted(PROJECT_READ_METHODS),
        },
    )


class Broker:
    def __init__(self, path, client=None):
        self.client = client or Client()
        self.path, self.calls = path, []

    def read(self, context, method, params):
        wire_arguments(method, params)
        self.calls.append((context.record(), method, deepcopy(params)))
        if method == "project_session":
            result = self.client.identity
        elif method == "project_observe":
            _, result = observations(self.path)
        elif method == "project_receipt":
            result = {
                "ok": True,
                "receipt_json": json.dumps(self.client.receipt),
                "run_context_checked": True,
            }
        else:
            pytest.fail("Unexpected native method")
        return {"valid": True, "project": {"ok": True, **result}}


def tools(path, broker):
    registry = ToolRegistry()
    register_project(registry, broker, path)
    return registry


def test_shared_registration_and_preflight(tmp_path):
    broker = Broker(tmp_path)
    with SessionJournal(tmp_path, "shared") as journal:
        registry = context_tools(broker, journal)
        schemas = {r["name"]: r for r in registry.schemas()}
        for tool in PROJECT_TOOLS:
            if tool["name"] != "execute_circuit_operation":
                assert schemas[tool["name"]]["input_schema"] == tool["inputSchema"]
                assert schemas[tool["name"]]["description"] == tool["description"]
        assert schemas["execute_circuit_operation"]["annotations"]["readOnlyHint"] is False
        args, _ = observations(tmp_path)
        result = registry.execute(
            ToolCall("read", "preflight_circuit_project", args), bound(tmp_path)
        )
        assert result.status == "ok" and result.data["checks_passed"]
        assert result.data["write_qualified"] is False
        assert len(broker.calls) == 3
        assert all(row[0] == bound(tmp_path).record() for row in broker.calls)
        args["model_files"] = [str(tmp_path / "missing.scs")]
        result = registry.execute(
            ToolCall("fail", "preflight_circuit_project", args), bound(tmp_path)
        )
        assert result.status == "preflight_failed" and not result.data["checks_passed"]


def test_offline_recovery_uses_shared_journal_without_broker(tmp_path):
    client = Client()
    execute(client, tmp_path)
    broker = Broker(tmp_path, client)
    registry = tools(tmp_path, broker)
    args = {"operation": "create_circuit_from_plan", "request_id": "make", "refresh": False}
    result = registry.execute(ToolCall("offline", "get_circuit_operation", args), bound(tmp_path))
    assert result.status == "ok" and result.data["state"] == "offline_record"
    assert result.data["last_observation"]["receipt"]["saved"]
    assert not broker.calls and client.dispatches == 1
    args["refresh"] = True
    live = registry.execute(ToolCall("live", "get_circuit_operation", args), bound(tmp_path))
    assert live.status == "ok" and live.data["state"] == "retained_receipt"
    assert live.data["automatic_resume_allowed"] is False
    client.identity["session_id"] = "replacement"
    result = registry.execute(ToolCall("stale", "get_circuit_operation", args), bound(tmp_path))
    assert result.status == "needs_reconcile" and result.data["state"] == "session_mismatch"
    assert client.dispatches == 1


@pytest.mark.parametrize("refresh", [False, True])
def test_missing_execution_record_does_not_interrupt_codex_task(tmp_path, refresh):
    from sico.codex.backend import CodexBackend

    broker = Broker(tmp_path)
    args = {"operation": "create_circuit_library", "request_id": "ldo_copilotTest_lib_1",
            "refresh": refresh}
    with SessionJournal(tmp_path, "missing-operation") as journal:
        backend = CodexBackend(None, tools(tmp_path, broker), journal, bound(tmp_path))
        backend._begin("Continue after library preparation", bound(tmp_path), "input", "chat")
        backend.active, backend.cancelled = True, lambda: False
        try:
            reply = backend._execute("get_circuit_operation", args)
            result = json.loads(reply["content"][0]["text"])
            assert reply["isError"] and result["status"] == "operation_not_found"
            assert not result["data"]["record_found"]
            assert not result["data"]["automatic_resume_allowed"]
            assert result["data"]["request_id"] == args["request_id"]
            assert not broker.calls and broker.client.dispatches == 0
            assert not backend.stale and not backend.stale_reason
            assert not backend.state.task["pending_calls"]
            preflight, _ = observations(tmp_path)
            assert not backend._execute("preflight_circuit_project", preflight)["isError"]
            assert broker.client.dispatches == 0
            assert not any(e["kind"] == "task.needs_reconcile" for e in journal.events())
        finally:
            backend.close()


def test_corrupt_execution_record_still_requires_reconciliation(tmp_path):
    client = Client()
    execute(client, tmp_path)
    journal = OperationJournal(tmp_path)
    journal.path(journal.key("create_circuit_from_plan", "make")).write_text("{")
    broker = Broker(tmp_path, client)
    result = tools(tmp_path, broker).execute(ToolCall("read", "get_circuit_operation", {
        "operation": "create_circuit_from_plan", "request_id": "make", "refresh": False,
    }), bound(tmp_path))
    assert result.status == "needs_reconcile"
    assert not result.data["automatic_resume_allowed"]
    assert not broker.calls and client.dispatches == 1


@pytest.mark.parametrize(
    "receipt",
    [None, {"ok": False, "saved": False, "circuit_ref": "created:make", "error": "partial"}],
)
def test_unknown_or_partial_recovery_preserves_evidence(tmp_path, receipt):
    client = Client()
    execute(client, tmp_path)
    client.receipt = receipt
    result = tools(tmp_path, Broker(tmp_path, client)).execute(
        ToolCall(
            "read",
            "get_circuit_operation",
            {
                "operation": "create_circuit_from_plan",
                "request_id": "make",
            },
        ),
        bound(tmp_path),
    )
    assert result.status == ("ok" if receipt else "needs_reconcile")
    assert result.data["state"] in {"outcome_unknown", "partial_or_failed"}
    assert not result.data["automatic_resume_allowed"] and client.dispatches == 1


def test_workspace_mismatch_and_native_write_rejected(tmp_path):
    broker = Broker(tmp_path)
    args, _ = observations(tmp_path)
    result = tools(tmp_path, broker).execute(
        ToolCall("read", "preflight_circuit_project", args), bound(tmp_path / "other")
    )
    assert result.status == "needs_reconcile" and not broker.calls
    client = ProjectReadClient(broker, bound(tmp_path))
    with pytest.raises(ValueError, match="only supports"):
        client.call_project_native("aiProjectClaim", [])


def test_failed_receipt_query_allows_next_codex_read(tmp_path):
    from sico.codex.backend import CodexBackend

    client = Client()
    execute(client, tmp_path)
    client.receipt = dict(ok=False, saved=False, circuit_ref="created:make", stage="instances",
                          target=["Work", "LDO", "schematic"], error="CDF parameter conflict")
    broker = Broker(tmp_path, client)
    with SessionJournal(tmp_path, "read-recovery") as journal:
        backend = CodexBackend(None, tools(tmp_path, broker), journal, bound(tmp_path))
        backend._begin("Inspect failed creation", bound(tmp_path), "input", "chat")
        backend.active, backend.cancelled = True, lambda: False
        try:
            result = backend._execute("get_circuit_operation", dict(
                operation="create_circuit_from_plan", request_id="make"))
            data = json.loads(result["content"][0]["text"])["data"]
            assert not result["isError"] and data["receipt"]["ok"] is False
            assert not data["automatic_resume_allowed"] and not backend.stale
            preflight, _ = observations(tmp_path)
            assert not backend._execute("preflight_circuit_project", preflight)["isError"]
            assert client.dispatches == 1
            assert not any(e["kind"] == "task.needs_reconcile" for e in journal.events())
        finally:
            backend.close()


def test_finite_project_transport_rejects_injection_and_wrong_receipt():
    good = {
        "targets": [["Work", "Dut", "schematic"]],
        "libraries": ["Work"],
        "presentation": "foreground",
    }
    assert wire_arguments("project_observe", good) == ["foreground", "Work,Dut,schematic", "Work"]
    for value in ["Work,Injected", "Work;Injected", 'Work")exit()', "Work\nOther"]:
        with pytest.raises(ValueError):
            wire_arguments("project_observe", {**good, "libraries": [value]})
    with pytest.raises(ValueError, match="key differs"):
        wire_arguments(
            "project_receipt",
            {
                "session_id": "original",
                "key": "config:make",
                "input_digest": "a" * 64,
                "operation": "draw_symbol",
            },
        )
    with pytest.raises(CircuitSpecError):
        wire_arguments("project_session", {"candidate": "a" * 32, "code": "exit()"})
    with ContextBroker() as broker:
        with pytest.raises(QueryUnavailable):
            broker.read(
                BoundContext("i", "g", "bound", {}), "project_session", {"candidate": "a" * 32}
            )


def test_failed_project_check_returns_to_model_for_explanation(tmp_path):
    args, _ = observations(tmp_path)
    args["model_files"] = [str(tmp_path / "missing.scs")]
    broker = Broker(tmp_path)
    provider = ScriptedProvider(
        [
            [
                ModelTurn(
                    "",
                    (
                        ToolCall("first", "preflight_circuit_project", args),
                    ),
                )
            ],
            [TextDelta("缺少模型文件，请指定已有模型路径。"), ModelTurn("缺少模型文件，请指定已有模型路径。")],
        ]
    )
    with SessionJournal(tmp_path, "queue") as journal:
        loop = PythonBackend(provider, tools(tmp_path, broker), journal, bound(tmp_path))
        events = list(loop.run("检查项目环境"))
        assert events[-1]["kind"] == "task.completed"
        finished = [e for e in events if e["kind"] == "tool.finished"]
        assert len(finished) == 1 and finished[0]["payload"]["id"] == "first"
        assert finished[0]["payload"]["result"]["status"] == "preflight_failed"
        assert not loop.state.task["pending_calls"]
        assert any(e["kind"] == "model.completed" and "模型路径" in e["payload"]["text"] for e in events)


def test_codex_adapter_allows_correcting_failed_preflight(tmp_path):
    from sico.codex.backend import CodexBackend

    args, _ = observations(tmp_path)
    args["model_files"] = [str(tmp_path / "missing.scs")]
    broker = Broker(tmp_path)
    with SessionJournal(tmp_path, "codex-project") as journal:
        backend = CodexBackend(None, tools(tmp_path, broker), journal, bound(tmp_path))
        backend._begin("检查环境", bound(tmp_path), "input", "chat")
        backend.active, backend.cancelled = True, lambda: False
        result = backend._execute("preflight_circuit_project", args)
        assert result["isError"] and not backend.stale
        count = len(broker.calls)
        args["model_files"] = []
        corrected = backend._execute("preflight_circuit_project", args)
        assert not corrected["isError"] and len(broker.calls) > count
        finished = [e for e in journal.events() if e["kind"] == "tool.finished"]
        assert len(finished) == 2
        assert finished[0]["payload"]["result"]["status"] == "preflight_failed"
        assert not backend.stale_reason
        backend.close()
