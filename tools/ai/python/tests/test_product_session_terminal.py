"""A real product session owns command-scoped terminal input ownership."""

from types import SimpleNamespace
import base64
from contextlib import contextmanager

import pytest

from session_terminal_fixtures import ControlledBackend
from sico.service.terminal_backend import TerminalCompletion
from sico.core.contracts import BoundContext, ToolCall
from sico.core.tools import ToolRegistry
from sico.adapters.terminal import register_terminal
from sico.adapters.tool_profiles import host_scope
from sico.codex.terminal_route import options as route_options
from sico.service.project_sessions import ProjectSessionOwner
from sico.service.session_terminal import SessionTerminal
from sico.service.terminal_command_profile import CommandShellProfile, command_claim
from sico.service.service_session_commands import session_command
from sico.transport.framing import ProtocolError


class Bridge:
    def __init__(self, _descriptor):
        self.closed = False

    def register_session(self, _session_id, _context):
        pass

    def register_target(self, _context):
        pass

    def close(self):
        self.closed = True


def context(project):
    return BoundContext("instance", "generation", "target",
                        {"cwd": str(project), "valid": True})


def gated_pty(backend, command, output, cancelled):
    assert backend.release.wait(4)
    assert not cancelled()
    output("pty", command.ref.command_id.encode())
    return TerminalCompletion(0, output_complete=True, shell_ready=True)


class Control:
    @contextmanager
    def guard(self, _address):
        yield "lease"

    def require(self, *_args):
        pass


class Worker:
    def _request(self, operation, _lease=None):
        return SimpleNamespace(result=lambda: operation(_lease))


def frontend_call(controller, operation, args=(), kwargs=None):
    workspace = SimpleNamespace(controllers={controller.session_id: controller}, worker=Worker())
    address = SimpleNamespace(session=SimpleNamespace(
        session_id=controller.session_id, runtime_id=controller.runtime_id))
    wire = SimpleNamespace(connection=SimpleNamespace(
        identity=SimpleNamespace(client_id="client")), operation=operation, control=None)
    return session_command(workspace, Control(), address, wire, operation, args, kwargs or {})


def test_command_profile_is_not_a_general_login_shell(tmp_path):
    profile = CommandShellProfile()
    assert profile.record() == {
        "shell": "/bin/bash", "mode": "pty", "queue_limit": 64,
        "sources_user_startup": False, "purpose": "sico.command",
    }
    claim = command_claim("session", str(tmp_path), "environment")
    assert claim.shell == "/bin/bash" and claim.mode == "pty"
    with pytest.raises(ValueError, match="profile"):
        CommandShellProfile(sources_user_startup=True)
    with pytest.raises(ValueError, match="absolute"):
        command_claim("session", "relative", "environment")
    with pytest.raises(ValueError, match="profile"):
        command_claim("session", str(tmp_path), "environment", queue_limit=0)
    assert route_options(True) == {
        "features.shell_tool": False, "features.shell_snapshot": False,
        "features.unified_exec": False, "features.unified_exec_tty": False,
    }
    assert route_options(False) == {}


def test_product_session_commits_terminal_input_ownership_and_cleans_up(
        tmp_path, monkeypatch):
    backends = []
    monkeypatch.setattr(
        "sico.service.project_sessions.provider_from_config",
        lambda _config, environment=None: SimpleNamespace(label="test", base="test", model="test"))

    def terminal_factory(journal, current):
        backend = ControlledBackend(run=gated_pty)
        backends.append(backend)
        return SessionTerminal(
            journal, current, cwd=tmp_path, environment_id="environment",
            backend_factory=lambda: backend, host_id="host", boot_id="boot")

    owner = ProjectSessionOwner(tmp_path, environment={}, bridge_factory=Bridge,
                                terminal_factory=terminal_factory)
    controller = owner.open("session", {}, context(tmp_path),
                            provider_config={"model": "test"})
    try:
        terminal = controller.terminal
        assert isinstance(terminal, SessionTerminal)
        assert terminal.snapshot()["opened"] is False
        request = terminal.request("command", "request", text="pwd")
        assert request.mode == "pty" and request.shell == "/bin/bash"
        terminal.submit(request)
        backend = backends[0]
        assert backend.entered.wait(2)
        assert frontend_call(controller, "terminal_status")["terminal"]["phase"] == "busy"
        registry = ToolRegistry()
        register_terminal(registry, terminal)
        scope = host_scope("desktop_core", registry.schemas())
        assert {"terminal_exec", "terminal_status", "terminal_wait",
                "terminal_write_input", "terminal_cancel"} <= set(scope["tools"])
        tool = registry.execute(ToolCall("call", "terminal_status", {
            "command": request.ref.record()}), context(tmp_path))
        assert tool.status == "ok" and tool.data["state"]["phase"] == "running"
        frontend_call(controller, "terminal_take_input", kwargs={
            "command": request.ref.record(), "owner": "user"})
        encoded = base64.b64encode(b"typed").decode()
        frontend_call(controller, "terminal_write_input", kwargs={
            "command": request.ref.record(), "owner": "user", "data": encoded})
        assert backend.inputs[0].data == b"typed" and backend.inputs[0].owner == "user"
        with pytest.raises(ProtocolError, match="input encoding"):
            frontend_call(controller, "terminal_write_input", kwargs={
                "command": request.ref.record(), "owner": "user", "data": "not base64"})
        assert controller.pending_work().jobs >= 1
        backend.release.set()
        state = terminal.wait(request.ref, 2)
        assert state.succeeded and state.output_complete
        assert controller.pending_work().jobs == 0
        history = frontend_call(controller, "terminal_history", kwargs={
            "after": 0, "limit": 1})
        assert history["contract"] == "sico.terminal.commands.v2"
        assert request.ref.record() in [row["ref"] for row in history["commands"]]
        row = next(item for item in history["commands"]
                   if item["ref"] == request.ref.record())
        assert row["command"] == "pwd" and row["timestamp"] and row["mode"] == "pty"
        with pytest.raises(ProtocolError, match="history limit"):
            frontend_call(controller, "terminal_history", kwargs={
                "after": 0, "limit": 21})
        page = frontend_call(controller, "terminal_read", kwargs={
            "command": request.ref.record(), "offset": 0, "limit": 64})
        assert page["command"]["ref"] == request.ref.record() and page["fragments"]
        executed = registry.execute(ToolCall("exec", "terminal_exec", {
            "command": "printf done"}), context(tmp_path))
        assert executed.status == "ok" and executed.data["state"]["phase"] == "exited"
    finally:
        owner.close()
    assert backends[0].closed.is_set()
    assert controller.terminal.snapshot()["closed"] is True


def test_session_without_a_qualified_terminal_factory_has_no_shell(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "sico.service.project_sessions.provider_from_config",
        lambda _config, environment=None: SimpleNamespace(label="test", base="test", model="test"))
    with ProjectSessionOwner(tmp_path, environment={}, bridge_factory=Bridge) as owner:
        controller = owner.open("session", {}, context(tmp_path),
                                provider_config={"model": "test"})
        assert controller.terminal is None
