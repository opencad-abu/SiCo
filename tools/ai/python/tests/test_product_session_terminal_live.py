"""Opt-in proof that a real product session owns a real managed PTY command."""

from types import SimpleNamespace
import time

from exec_live_fixtures import managed_configuration
from sico.core.contracts import BoundContext
from sico.core.terminal_output import OutputChunk
from sico.execution.managed_bash import ManagedBashBackend
from sico.process_identity import boot_id
from sico.service.project_sessions import ProjectSessionOwner
from sico.service.session_terminal import SessionTerminal
from sico.storage.terminal_records import JOURNAL_KIND, restore


class Bridge:
    def __init__(self, _descriptor):
        self.closed = False

    def register_session(self, _session_id, _context):
        pass

    def register_target(self, _context):
        pass

    def close(self):
        self.closed = True


def wait_running(terminal, ref, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if terminal.command(ref).phase == "running":
            return
        time.sleep(.01)
    raise AssertionError("terminal command did not reach running")


def test_product_session_owns_managed_pty_input(managed_configuration, tmp_path, monkeypatch):
    binary, control, child, work, home = managed_configuration
    backends = []
    monkeypatch.setattr(
        "sico.service.project_sessions.provider_from_config",
        lambda _config, environment=None: SimpleNamespace(label="test", base="test", model="test"))
    current = BoundContext("instance", "generation", "target",
                           {"cwd": str(work), "valid": True})

    def factory(journal, context):
        backend = ManagedBashBackend(
            binary, control, cwd=work, home=home, environment=child,
            environment_id="environment",
            access=dict(sandbox="read-only", approval="never", network=False))
        backends.append(backend)
        return SessionTerminal(
            journal, context, cwd=work, environment_id="environment",
            backend_factory=lambda: backend, host_id="product-live", boot_id=boot_id())

    owner = ProjectSessionOwner(work, environment={}, bridge_factory=Bridge,
                                terminal_factory=factory)
    controller = owner.open("session", {}, current, provider_config={"model": "test"})
    try:
        terminal = controller.terminal
        request = terminal.request(
            "command", "request", text='read line; printf "got:%s" "$line"')
        terminal.submit(request)
        wait_running(terminal, request.ref)
        terminal.take_input(request.ref, "user")
        terminal.write_input(request.ref, "user", b"managed" + bytes((10,)))
        state = terminal.wait(request.ref, 10)
        assert state.succeeded and state.output_complete and state.input_bytes == 8
        captured = bytearray()
        for row in controller.journal.records:
            if row["kind"] != JOURNAL_KIND:
                continue
            fact = restore(row["payload"], controller.journal)
            if fact["kind"] == "output":
                captured.extend(OutputChunk.from_record(fact["chunk"]).data)
        assert b"got:managed" in captured
    finally:
        owner.close()
    assert backends[0]._retirement is not None


def test_route_flag_builds_product_terminal_from_provider(managed_configuration, monkeypatch):
    binary, _control, child, work, _home = managed_configuration
    provider = SimpleNamespace(
        label="test", base="test", model="test", executable=lambda: str(binary))
    monkeypatch.setattr(
        "sico.service.project_sessions.provider_from_config",
        lambda _config, environment=None: provider)
    current = BoundContext("instance", "generation", "target",
                           {"cwd": str(work), "valid": True})
    environment = dict(child)
    environment["SICO_TERMINAL_ROUTE"] = "1"
    owner = ProjectSessionOwner(work, environment=environment, bridge_factory=Bridge)
    controller = owner.open("session", {}, current, provider_config={"model": "test"})
    try:
        terminal = controller.terminal
        assert isinstance(terminal, SessionTerminal)
        request = terminal.request("route", "route-request", text="printf route")
        terminal.submit(request)
        state = terminal.wait(request.ref, 10)
        assert state.succeeded and state.output_complete
    finally:
        owner.close()
