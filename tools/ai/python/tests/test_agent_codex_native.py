"""Actual 0.154 app-server and stdio MCP, with an isolated local Responses gateway."""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from sico.codex.backend import CodexBackend
from sico.codex.settings import CodexSettings
from sico.core.contracts import BoundContext, ToolResult
from sico.core.tools import ReadTool, ToolRegistry, no_arguments
from sico.adapters.terminal import register_terminal
from sico.core.terminal_commands import CommandRef, CommandRequest
from sico.core.terminal_state import CommandState
from sico.service.controller import SessionController
from sico.storage.journal import SessionJournal

CONTEXT = BoundContext("instance", "generation", "window_a", {"valid": True})


def sse(*items):
    events = [{"type": "response.created", "response": {"id": "resp-test"}}]
    for item in items:
        events += [
            {"type": "response.output_item.added", "output_index": 0, "item": item},
            {"type": "response.output_item.done", "output_index": 0, "item": item},
        ]
    events.append(
        {
            "type": "response.completed",
            "response": {
                "id": "resp-test",
                "output": list(items),
                "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            },
        }
    )
    return b"".join(
        ("event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n").encode()
        for event in events
    )


def answer(text):
    return sse(
        {
            "type": "message",
            "role": "assistant",
            "id": "msg-test",
            "content": [{"type": "output_text", "text": text}],
        }
    )


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        """OpenAI-compatible model listing; not part of model request accounting."""
        payload = json.dumps({"data": [{
            "id": "cad-copilot-test",
            "display_name": "CAD Copilot Test",
            "supported_reasoning_levels": [
                {"effort": "low", "description": "Fast"},
                {"effort": "medium", "description": "Balanced"},
                {"effort": "high", "description": "Deep"},
            ],
            "default_reasoning_level": "medium",
            "input_modalities": ["text", "image"],
            "service_tiers": [{"id": "priority", "name": "Fast",
                               "description": "1.5x speed, increased usage"}],
        }]}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):  # noqa: N802
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.paths.append(self.path)
        self.server.requests.append(request)
        self.server.authorizations.append(self.headers.get("Authorization"))
        try:
            body = self.server.responses.get_nowait()
        except queue.Empty:
            self.send_error(500)
            return
        if callable(body):
            body = body(request)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass

    def log_message(self, *args):
        pass


@pytest.fixture
def native_codex():
    path = os.environ.get("CAD_TEST_COPILOT_CODEX_CLI") or shutil.which("codex")
    if not path:
        pytest.skip("Set CAD_TEST_COPILOT_CODEX_CLI to original Codex 0.162.1")
    result = subprocess.run([path, "--version"], capture_output=True, timeout=10)
    if result.stdout.strip() != b"codex-cli 0.162.1":
        if os.environ.get("CAD_TEST_COPILOT_CODEX_CLI"):
            pytest.fail("Explicit native acceptance binary must report codex-cli 0.162.1")
        pytest.skip("Copilot native tests require Codex 0.162.1")
    return path


@pytest.fixture
def gateway():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.responses = queue.Queue()
    server.requests, server.authorizations = [], []
    server.paths = []
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def settings(gateway, native_codex, tool_format):
    options = {} if tool_format == "switch" else {"tool_format": tool_format}
    return CodexSettings(
        {
            "backend": "codex",
            "provider": "responses",
            "base_url": f"http://127.0.0.1:{gateway.server_port}/v1",
            "model": "cad-copilot-test",
            "codex_executable": native_codex,
            **options,
            "timeout": 10,
        },
        {
            **os.environ,
            "SICO_LEGACY_0114": "1" if tool_format == "switch" else "",
            "SICO_API_KEY": "copilot-test-secret",
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
        },
    )


def registry(seen):
    tools = ToolRegistry()

    def context(inputs, target):
        seen.append(target.target_id)
        return ToolResult("ok", "窗口上下文", {"target": target.target_id})

    tools.register(
        ReadTool(
            "get_context",
            "Read captured Virtuoso target",
            {"type": "object", "properties": {}, "additionalProperties": False},
            no_arguments,
            context,
        )
    )
    return tools


@pytest.mark.parametrize(("filename", "token"), [
    ("AGENTS.md", "PROJECT_AGENTS_PROOF"),
    ("AGENT.md", "PROJECT_AGENT_FALLBACK_PROOF"),
])
def test_native_project_instructions_are_loaded(
    tmp_path, native_codex, gateway, filename, token
):
    project = tmp_path / filename.split(".")[0].lower()
    project.mkdir()
    (project / filename).write_text(
        f"# Project instructions\n\n{token}\n", encoding="utf-8")
    provider = settings(gateway, native_codex, "native")
    gateway.responses.put(answer("Project instructions acknowledged."))
    with SessionJournal(project, "instructions") as journal:
        backend = CodexBackend(provider, registry([]), journal, CONTEXT)
        try:
            events = list(backend.run("Acknowledge the project instructions."))
            assert events[-1]["kind"] == "task.completed", events
            request = json.dumps(gateway.requests[0], ensure_ascii=False)
            assert token in request
        finally:
            backend.close()


@pytest.mark.project_memory
def test_project_memory_is_shared_by_sessions(tmp_path, native_codex, gateway):
    project = tmp_path / "memory-project"
    project.mkdir()
    provider = settings(gateway, native_codex, "native")
    with SessionJournal(project, "first") as journal:
        backend = CodexBackend(provider, registry([]), journal, CONTEXT)
        try:
            gateway.responses.put(answer("First project session."))
            events = list(backend.run("Create the project memory home."))
            assert events[-1]["kind"] == "task.completed", events
            home = backend.runtime.home
            assert home == journal.root / "codex"
        finally:
            backend.close()
    memories = home / "memories"
    memories.mkdir(mode=0o700, exist_ok=True)
    (memories / "memory_summary.md").write_text(
        "v1\nPROJECT_MEMORY_PROOF\n", encoding="utf-8")
    with SessionJournal(project, "second") as journal:
        backend = CodexBackend(provider, registry([]), journal, CONTEXT)
        try:
            gateway.responses.put(answer("Second project session."))
            events = list(backend.run("Use the project memory."))
            assert events[-1]["kind"] == "task.completed", events
            assert backend.runtime.home == home
            assert "PROJECT_MEMORY_PROOF" in json.dumps(
                gateway.requests[-1], ensure_ascii=False)
        finally:
            backend.close()


@pytest.mark.parametrize("tool_format", ["flat", "native", "switch"])
def test_native_app_server_mcp_continuation_and_resume(
    tmp_path, native_codex, gateway, tool_format
):
    name = (
        {"name": "mcp__sico_ic__get_context"}
        if tool_format in {"flat", "switch"}
        else {
            "namespace": "mcp__sico_ic",
            "name": "get_context",
        }
    )
    gateway.responses.put(
        sse(
            {
                "type": "function_call",
                "id": "fc-test",
                "call_id": "call-test",
                "arguments": "{}",
                **name,
            }
        )
    )
    gateway.responses.put(answer("已读取当前窗口。"))
    gateway.responses.put(answer("继续使用同一会话。"))
    seen = []
    provider = settings(gateway, native_codex, tool_format)
    from cadai.codex_selection import CODEX_VERSION

    assert provider.label.startswith("Codex " + CODEX_VERSION)
    with SessionJournal(tmp_path, "native") as journal:
        backend = CodexBackend(provider, registry(seen), journal, CONTEXT)
        backend.tool_profile.select("desktop_compat")
        try:
            startup = list(
                backend.initialize_context(lambda: False, context=CONTEXT, input_id="init")
            )
            assert startup[-1]["kind"] == "task.completed", startup
            assert not gateway.requests
            events = list(backend.run("读取当前窗口", input_id="first"))
            assert events[-1]["kind"] == "task.completed", events
            assert backend.state.task["text"] == "已读取当前窗口。"
            thread_id = backend.thread_id
            assert len(gateway.requests) == 2
            assert seen == ["window_a"] * 3  # startup, captured facts, actual model MCP call
            assert not backend.state.messages
            for request in gateway.requests:
                assert request["model"] == "cad-copilot-test"
                namespaces = [t for t in request["tools"] if t["type"] == "namespace"]
                assert bool(namespaces) is (tool_format == "native")
                definitions = [c for t in request["tools"] for c in t.get("tools", [t])]
                assert any(t.get("name") == "exec_command" for t in definitions), definitions
            outputs = [
                i
                for i in gateway.requests[1]["input"]
                if i.get("type") == "function_call_output" and i.get("call_id") == "call-test"
            ]
            assert len(outputs) == 1 and "window_a" in json.dumps(outputs[0])
            assert "copilot-test-secret" not in json.dumps(journal.events())
        finally:
            backend.close()
    with SessionJournal(tmp_path, "native") as journal:
        backend = CodexBackend(provider, registry(seen), journal, CONTEXT)
        try:
            events = list(backend.run("继续分析", input_id="second"))
            assert events[-1]["kind"] == "task.completed", events
            assert backend.thread_id == thread_id
            assert backend.state.task["text"] == "继续使用同一会话。"
            assert len(gateway.requests) == 3
            assert "读取当前窗口" in json.dumps(gateway.requests[2], ensure_ascii=False)
            assert gateway.authorizations == ["Bearer copilot-test-secret"] * 3
        finally:
            backend.close()


class FakeTerminal:
    def __init__(self):
        self.submitted = []

    def request(self, request_id, command_id, *, text=None, cwd=None):
        return CommandRequest(
            CommandRef("native", "terminal", command_id), request_id, cwd=cwd,
            environment_id="environment", mode="pty", shell="/bin/bash", text=text)

    def submit(self, request):
        self.submitted.append(request)

    def wait(self, _ref, timeout=None):
        return None

    def command(self, _ref):
        return CommandState(
            self.submitted[-1], phase="exited", exit_code=0,
            output_complete=True)

    def output_preview(self, _ref, limit=8192):
        return "fake-terminal-output"


def test_native_terminal_route_exposes_terminal_exec_without_native_shell(
        tmp_path, native_codex, gateway):
    gateway.responses.put(sse({
        "type": "function_call", "id": "fc-terminal", "call_id": "call-terminal",
        "name": "mcp__sico_ic__terminal_exec",
        "arguments": json.dumps({"command": "echo routed"}),
    }))
    gateway.responses.put(answer("终端路由完成。"))
    terminal = FakeTerminal()
    tools = registry([])
    register_terminal(tools, terminal)
    with SessionJournal(tmp_path, "native-terminal-route") as journal:
        backend = CodexBackend(
            settings(gateway, native_codex, "flat"), tools, journal, CONTEXT,
            terminal=terminal)
        try:
            events = list(backend.run("通过终端执行", input_id="terminal-route"))
        finally:
            backend.close()
    assert events[-1]["kind"] == "task.completed", events
    definitions = [child for item in gateway.requests[0]["tools"]
                   for child in item.get("tools", [item])]
    names = {child.get("name") for child in definitions if child.get("name")}
    assert any(name.endswith("terminal_exec") for name in names)
    assert "exec_command" not in names
    assert terminal.submitted and terminal.submitted[0].text == "echo routed"
    assert "fake-terminal-output" in json.dumps(gateway.requests[1])


def test_cancel_inflight_model_and_resume_without_replaying_input(tmp_path, native_codex, gateway):
    entered, release = threading.Event(), threading.Event()

    def blocked(request):
        entered.set()
        assert release.wait(10)
        return answer("迟到的回复不应显示")

    gateway.responses.put(blocked)
    gateway.responses.put(answer("取消后继续"))
    provider = settings(gateway, native_codex, "flat")
    with SessionJournal(tmp_path, "cancel") as journal:
        backend = CodexBackend(provider, registry([]), journal, CONTEXT)
        controller = SessionController(backend)
        try:
            controller.submit("第一个需求")
            assert entered.wait(5)
            other = BoundContext("instance", "generation", "window_b", {"valid": True})
            assert controller.accept(
                {
                    "kind": "submit",
                    "id": "window_b",
                    "text": "第二个需求",
                    "context": other.record(),
                }
            )
            controller.cancel()
            assert controller.wait(8)
            assert not controller.fault
            assert backend.state.task["status"] == "cancelled"
            assert len(gateway.requests) == 1 and controller.paused
            assert backend.runtime is None
            release.set()
            controller.resume()
            assert controller.wait(8)
            assert backend.state.task["status"] == "completed", journal.events()
            assert backend.state.context.target_id == "window_b"
            assert backend.state.task["text"] == "取消后继续"
            starts = [e for e in journal.events() if e["kind"] == "task.started"]
            assert len(starts) == 2 and len(gateway.requests) == 2
        finally:
            release.set()
            controller.close()
            assert controller.wait(10)


def test_codex_backend_drives_qmainwindow_workspace(tmp_path, native_codex, gateway):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from agent_gui_wait import settle, wait_until
    from PyQt5.QtWidgets import QApplication

    from agent_window_fixture import create_window as AssistantWindow

    gateway.responses.put(answer("QMainWindow 文档渲染完成。"))
    provider = settings(gateway, native_codex, "flat")
    app = QApplication.instance() or QApplication([])
    with SessionJournal(tmp_path, "gui") as journal:
        backend = CodexBackend(provider, registry([]), journal, CONTEXT)
        window = AssistantWindow(backend)
        window.show()
        try:
            window.controller.initialize_context()
            settle(window, timeout=20)
            assert not window.controller.busy
            # Controller completion can precede its published UI snapshot.
            # Submit a new task only after the window has observed that completion.
            wait_until(lambda: not window.presentation.state["busy"]
                       and window.presentation.state["task"].get("status") == "completed")
            window.input.setPlainText("请在主窗口显示结果")
            window.actions.submit()
            settle(window, timeout=20)
            window.binding.poll()
            app.processEvents()
            window.renderer.flush()
            assert backend.state.task["status"] == "completed"
            starts = [event for event in journal.events() if event["kind"] == "task.started"]
            assert starts[-1]["payload"]["text"] == "请在主窗口显示结果"
            assert starts[-1]["task_id"] == backend.state.task["id"]
            wait_until(lambda: "QMainWindow 文档渲染完成。" in window.display.toPlainText(), timeout=5)
            assert window.panels["left"].objectName() == "leftDock"
            assert window.panels["right"].objectName() == "rightDock"
            assert window.input.toPlainText() == ""
        finally:
            window.lifecycle.closing = True
            window.close()
            window.controller.wait(10)
