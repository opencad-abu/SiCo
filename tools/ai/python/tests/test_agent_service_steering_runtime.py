"""Remote Qt steering and queued drafts reach the original Codex runtime."""

import json
import threading

import pytest
from agent_gui_wait import wait_until
from agent_service_gui_fixtures import remote_window
from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from test_agent_codex_native import answer, gateway, native_codex, settings, sse  # noqa: F401

from sico.codex.backend import CodexBackend
from sico.codex.rpc import AppServer
from sico.core.contracts import ToolResult
from sico.core.tools import ReadTool, ToolRegistry, no_arguments


@pytest.mark.parametrize("tool_format", ["native", "flat"])
@pytest.mark.parametrize("boundary", ["model", "tool"])
def test_remote_composer_steers_then_queues_a_complete_draft(
        tmp_path, monkeypatch, gateway, native_codex, tool_format, boundary):
    entered, release = threading.Event(), threading.Event()
    calls = []
    original = AppServer.request

    def request(self, method, params, **kwargs):
        calls.append((method, params))
        return original(self, method, params, **kwargs)

    def first(_request):
        entered.set()
        assert release.wait(20)
        return answer("Initial reading complete.")

    def probe(_inputs, target):
        entered.set()
        assert release.wait(20)
        return ToolResult("ok", "Probe complete", {"target": target.target_id})

    tools = ToolRegistry()
    tools.register(ReadTool("probe", "Read the captured target",
        {"type": "object", "properties": {}, "additionalProperties": False},
        no_arguments, probe))
    monkeypatch.setattr(AppServer, "request", request)
    monkeypatch.setattr("sico.service.project_sessions.create_backend",
        lambda provider, _tools, journal, context:
            CodexBackend(provider, tools, journal, context))
    provider = settings(gateway, native_codex, tool_format)
    provider.release = release
    name = ({"namespace": "mcp__sico_ic", "name": "probe"}
            if tool_format == "native" else {"name": "mcp__sico_ic__probe"})
    gateway.responses.put(first if boundary == "model" else sse({
        "type": "function_call", "id": "fc-probe", "call_id": "call-probe",
        "arguments": "{}", **name,
    }))
    gateway.responses.put(answer("Applied revised limit 17 to this task."))
    gateway.responses.put(answer("Completed the separate queued task."))

    with remote_window(tmp_path, monkeypatch, provider=provider) as (service, window):
        controller = service.owner.controllers["live"]
        try:
            wait_until(window.steering_input.available)
            window.input.setPlainText("Inspect the captured target.")
            QTest.keyClick(window.input, Qt.Key_Return)
            wait_until(entered.is_set, timeout=10)
            wait_until(lambda: window.send.isEnabled()
                       and window.send.text() == "补充当前任务", timeout=8)
            scope = window.presentation.state["steering"]
            window.input.setPlainText("Use revised limit 17.")
            if boundary == "model":
                QTest.keyClick(window.input, Qt.Key_Return)
            else:
                window.send.click()
            wait_until(lambda: not window.input.toPlainText(), timeout=8)
            assert controller.busy and not release.is_set()
            assert controller._task["id"] == scope["task_id"]
            assert not controller.inbox.pending
            assert len(controller.loop.steering.rows) == 1
            assert next(iter(controller.loop.steering.rows.values()))["status"] == "accepted"

            window.input.setPlainText("Run the separate queued check.")
            window.input.turn_options = {"model": provider.model}
            wait_until(window.steering_input.queue_action.isEnabled)
            window.steering_input.queue_action.trigger()
            wait_until(lambda: len(controller.inbox.pending) == 1)
            wait_until(lambda: window.input.draft_content() == ("", None, [], None))
            queued = controller.inbox.pending[0]
            assert queued["message"]["text"] == "Run the separate queued check."
            assert queued["message"]["turn_options"]["model"] == provider.model
            assert controller._task["id"] == scope["task_id"]
            assert not release.is_set()

            release.set()
            wait_until(lambda: not controller.busy and not controller.inbox.pending, timeout=15)
            assert controller._task["status"] == "completed"
            assert len(gateway.requests) == 3
            assert "Use revised limit 17." in json.dumps(gateway.requests[1])
            assert "Run the separate queued check." not in json.dumps(gateway.requests[1])
            assert "Run the separate queued check." in json.dumps(gateway.requests[2])
            events = controller.journal.events()
            assert len([row for row in events if row["kind"] == "task.started"]) == 2
            assert len([row for row in events if row["kind"] == "task.completed"]) == 2
            steering = [params for method, params in calls if method == "turn/steer"]
            assert len(steering) == 1
            assert steering[0]["expectedTurnId"] == scope["turn_id"]
            assert steering[0]["threadId"] == scope["thread_id"]
            assert len([method for method, _ in calls if method == "turn/start"]) == 2
            assert not any(method == "turn/interrupt" for method, _ in calls)
            assert not window.binding.failed
        finally:
            release.set()
