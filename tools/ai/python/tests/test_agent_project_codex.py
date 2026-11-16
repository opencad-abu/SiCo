"""Actual 0.154 tool transport with shared project handlers and injected CAD observations."""

import json

import pytest
from test_agent_codex_native import answer, gateway, native_codex, settings, sse  # noqa: F401
from test_agent_project import Broker, bound, tools
from test_project_contract_boundaries import observations

from sico.codex.backend import CodexBackend
from sico.core.contracts import ToolResult
from sico.core.tools import ReadTool, no_arguments
from sico.storage.journal import SessionJournal


@pytest.mark.parametrize("tool_format", ["flat", "native"])
def test_codex_shared_preflight_failure_retained_and_explained(
    tmp_path, native_codex, gateway, tool_format  # noqa: F811 -- imported pytest fixtures
):
    args, _ = observations(tmp_path)
    args["model_files"] = [str(tmp_path / "missing.scs")]
    identity = (
        {"name": "mcp__sico_ic__preflight_circuit_project"}
        if tool_format == "flat"
        else {"namespace": "mcp__sico_ic", "name": "preflight_circuit_project"}
    )
    gateway.responses.put(
        sse(
            {
                "type": "function_call",
                "id": "fc-project",
                "call_id": "call-project",
                "arguments": json.dumps(args),
                **identity,
            }
        )
    )
    gateway.responses.put(answer("预检失败，待核对。"))
    broker = Broker(tmp_path)
    registry = tools(tmp_path, broker)
    registry.register(
        ReadTool(
            "get_context",
            "Read source",
            {"type": "object", "properties": {}, "additionalProperties": False},
            no_arguments,
            lambda inputs, context: ToolResult(data={"valid": True}),
        )
    )
    with SessionJournal(tmp_path, "codex-project") as journal:
        backend = CodexBackend(
            settings(gateway, native_codex, tool_format), registry, journal, bound(tmp_path)
        )
        try:
            events = list(backend.run("检查项目环境", input_id="project"))
            assert events[-1]["kind"] == "task.completed", events
            finished = [
                e
                for e in events
                if e["kind"] == "tool.finished"
                and e["payload"]["name"] == "preflight_circuit_project"
            ]
            assert len(finished) == 1
            result = finished[0]["payload"]["result"]
            assert result["status"] == "preflight_failed"
            assert result["data"]["checks_passed"] is False
            assert backend.state.task["text"] == "预检失败，待核对。"
            assert not backend.state.task["diagnostic"]
            assert len(broker.calls) == 3
            assert not backend.state.task["pending_calls"]
        finally:
            backend.close()
