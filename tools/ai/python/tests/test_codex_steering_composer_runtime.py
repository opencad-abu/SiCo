"""Primary Qt composer steers original Codex while a model or MCP tool is busy."""

import json
import threading

import pytest
from agent_gui_wait import settle, wait_until
from PyQt5.QtCore import QEvent, Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication
from test_agent_codex_native import (  # noqa: F401
    CONTEXT,
    answer,
    gateway,
    native_codex,
    settings,
    sse,
)

from sico.codex.backend import CodexBackend
from sico.codex.rpc import AppServer
from sico.core.contracts import ToolResult
from sico.core.tools import ReadTool, ToolRegistry, no_arguments
from sico.service.controller import SessionController
from sico.storage.journal import SessionJournal
from agent_window_fixture import create_window as AssistantWindow


@pytest.fixture(scope="session")
def app():
    application = QApplication.instance() or QApplication([])
    yield application
    application.processEvents()


@pytest.mark.parametrize("tool_format", ["native", "flat"])
@pytest.mark.parametrize("boundary", ["model", "tool"])
def test_primary_input_reaches_next_model_request_in_the_same_task(
    tmp_path, gateway, native_codex, app, monkeypatch, tool_format, boundary,  # noqa: F811
):
    entered, release = threading.Event(), threading.Event()
    calls, tool_calls = [], []
    original = AppServer.request

    def request(self, method, params, **kwargs):
        calls.append((method, params, threading.current_thread().name))
        return original(self, method, params, **kwargs)

    def first(_request):
        entered.set()
        assert release.wait(15)
        return answer("Initial reading complete.")

    def probe(inputs, target):
        tool_calls.append(target.target_id)
        entered.set()
        assert release.wait(15)
        return ToolResult("ok", "Probe finished", {"target": target.target_id})

    monkeypatch.setattr(AppServer, "request", request)
    tools = ToolRegistry()
    tools.register(ReadTool(
        "probe", "Read the captured target",
        {"type": "object", "properties": {}, "additionalProperties": False},
        no_arguments, probe,
    ))
    name = ({"namespace": "mcp__sico_ic", "name": "probe"}
            if tool_format == "native" else {"name": "mcp__sico_ic__probe"})
    gateway.responses.put(first if boundary == "model" else sse({
        "type": "function_call", "id": "fc-probe", "call_id": "call-probe",
        "arguments": "{}", **name,
    }))
    gateway.responses.put(answer("Applied revised limit 17 to this task."))
    with SessionJournal(tmp_path, "composer-steer") as journal:
        backend = CodexBackend(settings(gateway, native_codex, tool_format),
                               tools, journal, CONTEXT)
        controller = SessionController(backend)
        window = AssistantWindow(controller)
        window.show()
        try:
            settle(window)
            assert window.send.text() == "发送"
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
            wait_until(lambda: bool(backend.steering.rows) and all(
                row["status"] == "accepted" for row in backend.steering.rows.values()
            ), timeout=8)
            # Native acceptance happens while the original operation is still blocked.
            assert controller.busy and not release.is_set()
            assert not controller.inbox.pending
            release.set()
            settle(window, timeout=10)
            assert backend.state.task["status"] == "completed", backend.state.task
            assert len(gateway.requests) == 2
            assert "Use revised limit 17." in json.dumps(gateway.requests[-1])
            if boundary == "tool":
                outputs = [item for item in gateway.requests[-1]["input"]
                           if item.get("type") == "function_call_output"
                           and item.get("call_id") == "call-probe"]
                assert len(outputs) == 1 and "window_a" in json.dumps(outputs[0])
                assert tool_calls == ["window_a"]
            assert len([e for e in journal.events() if e["kind"] == "task.started"]) == 1
            assert len([c for c in calls if c[0] == "turn/start"]) == 1
            steering = [c for c in calls if c[0] == "turn/steer"]
            assert len(steering) == 1
            assert steering[0][1]["expectedTurnId"] == scope["turn_id"]
            assert steering[0][1]["threadId"] == scope["thread_id"]
            assert steering[0][2].startswith("copilot-session-")
            assert not any(c[0] == "turn/interrupt" for c in calls)
            assert not window.input.toPlainText() and not window.binding.failed
            assert len(window.presentation.transcript.supplements) == 1
            wait_until(lambda: window.send.text() == "发送" and window.send.isEnabled())
        finally:
            release.set()
            window.close()
            assert window.sessions.wait(8)
            window.deleteLater()
            app.sendPostedEvents(None, QEvent.DeferredDelete)
            app.processEvents()
