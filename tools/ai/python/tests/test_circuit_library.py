"""Copilot library dispatch, recovery and agent-facing metadata contracts."""

import json
from copy import deepcopy

import pytest
from test_agent_circuit import CircuitBroker
from test_agent_project import bound
from test_circuit_automation import selection

from cadai.circuit_guidance import INSTRUCTIONS
from cadai.circuit_library import call_library
from cadai.circuit_spec_schema import CircuitSpecError
from cadai.mcp import McpServer, _TOOLS_BY_NAME
from cadai.project_contract import receipt as checked_receipt
from cadai.project_journal import OperationJournal
from cadai.project_tools import call_project
from sico.adapters.circuit import CircuitClient
from sico.adapters.virtuoso import context_tools
from sico.core.contracts import ToolCall, ToolResult
from sico.core.tools import tool_message
from sico.storage.journal import SessionJournal
from sico.transport.circuit import wire_arguments


class LibraryBroker(CircuitBroker):
    def __init__(self, path, outcome="success"):
        super().__init__(path)
        self.outcome = outcome
        self.expected = ["task:t", "background", ["Work", "library", "library"],
                         "library", "library-prepare:p", str(path / "Work"), str(path)]

    def circuit_call(self, context, params):
        wire_arguments(params)
        if params["function"] != "aiLibraryCreate":
            return super().circuit_call(context, params)
        self.writes.append(deepcopy(params))
        assert params["expected"] == self.expected and params["claim"]
        self.client.receipt = dict(ok=True, saved=True, created=True, library="Work",
                                   library_ref="library:make", library_path=str(self.path / "Work"),
                                   technology_library="Tech", technology_path="/pdk/tech.db")
        if self.outcome == "partial":
            self.client.receipt.update(ok=False, saved=False, error="attach failed")
        if self.outcome == "unknown":
            self.client.receipt = None
        if self.outcome != "success":
            raise ConnectionError("reply lost")
        return dict(valid=True, circuit=self.client.receipt)

    def read(self, context, method, params):
        result = super().read(context, method, params)
        if method == "project_receipt":
            result["project"]["library_observation"] = [
                "Work", str(self.path / "Work"), "/pdk/tech.db"]
        return result


class HighLevelLibraryBroker(LibraryBroker):
    """Fake one-step library flow: task + prepare + describe + create."""

    reattach_required = False

    def __init__(self, path, outcome="success"):
        super().__init__(path, outcome)
        self.high_level_calls = []

    def circuit_call(self, context, params):
        self.high_level_calls.append(params["function"])
        if params["function"] == "aiCircuitBeginTask":
            return {"valid": True, "circuit": {
                "ok": True, "task_ref": "task:lib-test"}}
        if params["function"] == "aiLibraryPrepare":
            self.expected[0] = params["values"][0]
            return {"valid": True, "circuit": {
                "ok": True,
                "prepare_ref": "library-prepare:make",
                "library": "Work",
                "library_path": str(self.path / "Work"),
                "technology_library": params["values"][3],
                "technology_path": "/pdk/tech.db",
                "library_ready": False,
                "reattach_required": self.reattach_required,
            }}
        return super().circuit_call(context, params)


@pytest.mark.parametrize("outcome,state", [
    ("success", "retained_receipt"), ("lost", "retained_receipt"),
    ("partial", "partial_or_failed"), ("unknown", "outcome_unknown"),
])
def test_library_uses_own_preparation_and_journal_without_view_preflight(tmp_path, outcome, state):
    broker = LibraryBroker(tmp_path, outcome)
    client = CircuitClient(broker, bound(tmp_path), tmp_path)
    arguments = dict(operation="create_circuit_library",
                     arguments=dict(prepare_ref="library-prepare:p", request_id="make"))
    assert client.requirements is None
    first = call_project("execute_circuit_operation", arguments, client, tmp_path, _TOOLS_BY_NAME)
    assert first["state"] == state, first
    second = call_project("execute_circuit_operation", arguments, client, tmp_path, _TOOLS_BY_NAME)
    assert second["state"] == state and not second["automatic_resume_allowed"]
    assert sum(row["function"] == "aiLibraryCreate" for row in broker.writes) == 1
    assert OperationJournal(tmp_path).read("library-create:make")["adapter_binding"]["expected"] == broker.expected


def test_high_level_library_prepares_and_dispatches_without_view_task(tmp_path):
    broker = HighLevelLibraryBroker(tmp_path)
    client = CircuitClient(broker, bound(tmp_path), tmp_path)
    result = call_project(
        "execute_circuit_operation",
        {"operation": "create_circuit_library", "arguments": {
            "request_id": "make", "library": "Work",
            "technology_library": "Tech",
        }},
        client, tmp_path, _TOOLS_BY_NAME,
    )
    assert result["state"] == "retained_receipt", result
    assert result["receipt"]["library"] == "Work"
    assert broker.high_level_calls[:3] == [
        "aiCircuitBeginTask", "aiLibraryPrepare", "aiCopilotDescribe"]
    assert "aiLibraryCreate" in broker.high_level_calls
    assert client.requirements is None


def test_high_level_library_conflict_does_not_dispatch_a_write(tmp_path):
    broker = HighLevelLibraryBroker(tmp_path)
    broker.reattach_required = True
    client = CircuitClient(broker, bound(tmp_path), tmp_path)
    result = call_project(
        "execute_circuit_operation",
        {"operation": "create_circuit_library", "arguments": {
            "request_id": "make", "library": "Work",
        }},
        client, tmp_path, _TOOLS_BY_NAME,
    )
    assert result["code"] == "library_conflict"
    assert result["operation_dispatched"] is False
    assert all(row["function"] != "aiLibraryCreate" for row in broker.writes)


def test_high_level_library_allows_an_unbound_technology(tmp_path):
    broker = HighLevelLibraryBroker(tmp_path)
    client = CircuitClient(broker, bound(tmp_path), tmp_path)
    result = call_project(
        "execute_circuit_operation",
        {"operation": "create_circuit_library", "arguments": {
            "request_id": "make", "library": "Work",
        }},
        client, tmp_path, _TOOLS_BY_NAME,
    )
    assert result["state"] == "retained_receipt", result
    assert broker.high_level_calls[:2] == ["aiCircuitBeginTask", "aiLibraryPrepare"]


def test_unbound_library_receipt_allows_null_technology():
    record = {"operation": "create_circuit_library", "request_id": "unbound",
              "input_summary": {}}
    value = {
        "ok": True, "library_ref": "library:unbound", "library": "Unbound",
        "library_path": "/tmp/Unbound", "technology_library": None,
        "technology_path": None, "created": True, "saved": True,
    }
    assert checked_receipt(value, record)["technology_library"] is None
    with pytest.raises(CircuitSpecError, match="technology_library"):
        checked_receipt({**value, "technology_path": "/pdk/tech.db"}, record)


def test_prepare_library_creates_a_background_task_for_a_stale_task_ref(tmp_path):
    broker = HighLevelLibraryBroker(tmp_path)
    client = CircuitClient(broker, bound(tmp_path), tmp_path)
    ok, prepared = call_library(
        "prepare_library",
        {"task_ref": "d2818040_2", "request_id": "prep-1",
         "library": "Work", "technology_library": "Tech"},
        client, tmp_path,
    )
    assert ok and prepared["task_ref"] == "task:lib-test"
    assert broker.high_level_calls[0] == "aiCircuitBeginTask"


def test_library_direct_call_and_changed_workspace_never_dispatch(tmp_path):
    broker = LibraryBroker(tmp_path)
    client = CircuitClient(broker, bound(tmp_path), tmp_path)
    with pytest.raises(CircuitSpecError, match="execute_circuit_operation"):
        client.call("eval_skill_native", {"code": 'aiLibraryCreate("library-prepare:p" "make")'})
    broker.expected[6] = str(tmp_path / "other")
    with pytest.raises(CircuitSpecError, match="captured project changed"):
        client.prepare_operation("create_circuit_library", {"prepare_ref": "library-prepare:p"},
                                 broker.client.identity)
    assert all(row["function"] != "aiLibraryCreate" for row in broker.writes)
    server = McpServer(client, workspace=tmp_path)
    response = server.handle(dict(jsonrpc="2.0", id=1, method="tools/call", params=dict(
        name="create_circuit_library", arguments=dict(prepare_ref="library-prepare:p", request_id="make")
    )))
    assert "execute_circuit_operation" in response["error"]["message"]


def test_copilot_bind_defaults_compact_and_schema_discovery_includes_library_support(tmp_path, monkeypatch):
    import cadai.mcp as mcp

    _, service, args = selection()
    monkeypatch.setattr(mcp.McpServer, "pdk_bindings", lambda _: service)
    with SessionJournal(tmp_path, "metadata") as journal:
        registry = context_tools(LibraryBroker(tmp_path), journal)
        result = registry.execute(ToolCall("bind", "bind_pdk_device", args), bound(tmp_path))
        assert result.status == "ok", result
        assert result.data["parameter_count"] == 160 and result.data["metadata_retained"]
        assert "master" not in result.data and "binding_ref" not in result.summary
        for name in ("list_project_extensions", "prepare_library", "create_circuit_library"):
            schema = registry.execute(ToolCall("schema", "get_circuit_operation_schema",
                                               {"operation": name}), bound(tmp_path))
            assert schema.data["definition"] == _TOOLS_BY_NAME[name]
        legacy = registry.resolve("prepare_circuit_library")
        assert legacy is not None and legacy.name == "prepare_library"
        service.extension_reader = lambda _: {"items": []}
        missing = registry.execute(ToolCall("missing", "bind_pdk_device", args), bound(tmp_path))
        assert missing.status == "tool_error" and not missing.data["user_input_required"]
        assert "无需用户" in missing.summary


def test_continuation_summary_survives_large_plan_and_both_backends_use_shared_guidance(tmp_path):
    from cadai.mcp import SERVER_INSTRUCTIONS
    from sico.adapters.circuit import circuit_summary
    from sico.codex.backend import INSTRUCTIONS as CODEX_INSTRUCTIONS
    from sico.service.python_backend import PythonBackend
    from sico.core.tools import ToolRegistry
    from sico.providers.mock import ScriptedProvider

    assert INSTRUCTIONS in SERVER_INSTRUCTIONS and INSTRUCTIONS in CODEX_INSTRUCTIONS
    with SessionJournal(tmp_path, "instructions") as journal:
        loop = PythonBackend(ScriptedProvider([]), ToolRegistry(), journal, bound(tmp_path))
        assert INSTRUCTIONS in loop._system()
    data = dict(preview_digest="a" * 64, geometry_preview_ready=True,
                next_action="preview_circuit_geometry", user_input_required=False,
                plan={"large": "x" * 16000})
    message = tool_message(ToolCall("preview", "preview_circuit_spec", {}),
                           ToolResult("ok", circuit_summary("preview_circuit_spec", data), data),
                           8000, lambda _: {"path": "private-plan.json"})
    result = json.loads(message["content"])
    assert result["truncated"]
    summary = json.loads(result["summary"])
    assert summary["preview_digest"] == "a" * 64 and summary["geometry_preview_ready"]
