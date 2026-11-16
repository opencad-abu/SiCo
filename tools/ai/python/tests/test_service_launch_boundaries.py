"""Desktop pipe, bridge control and launch cleanup stay bounded."""

from __future__ import annotations

import io
import json
import sys
import threading
from types import SimpleNamespace

import pytest
from sico.core.contracts import BoundContext
from sico.service import launch
from sico.service.launch import DesktopPipe


class Child:
    def __init__(self):
        self.stdin = io.BytesIO()
        self.stdin.close_called = False

    def close_stream(self):
        self.stdin.close()


class FlakyChild(Child):
    def __init__(self):
        super().__init__()
        self.writes = 0

        def write(value):
            self.writes += 1
            if self.writes == 2:
                raise OSError("closed")
            return io.BytesIO.write(self.stdin, value)

        self.stdin.write = write


def test_desktop_pipe_buffers_until_attach_and_registers_submit(monkeypatch):
    value = DesktopPipe()
    targets, broker = [], []
    value.targets = SimpleNamespace(add=targets.append)
    value.broker = SimpleNamespace(register_target=broker.append)
    context = BoundContext("instance", "generation", "target", {"cwd": "/project"})
    monkeypatch.setattr(launch, "TargetRegistry", lambda _context: value.targets)
    monkeypatch.setattr(launch, "submission", lambda message: context)

    value({"kind": "notice"})
    value({"kind": "submit", "text": "x"})
    assert len(value.pending) == 2 and targets == [context]
    for index in range(22):
        value({"kind": "notice", "index": index})
    with pytest.raises(ValueError, match="queue is full"):
        value({"kind": "notice"})

    child = Child()
    value.attach(child, {"context": context.record()})
    assert targets[-1] is context and broker[-1] is context
    assert value.child is child and value.pending == []
    assert child.stdin.getvalue().splitlines()[0].startswith(b'{"context"')


def test_desktop_pipe_write_failure_stops_and_shutdown_is_terminal():
    value = DesktopPipe()
    child = FlakyChild()
    value.attach(child, {"context": BoundContext("i", "g", "t", {}).record()})
    value({"kind": "notice"})
    assert value.stopping is True
    value({"kind": "shutdown"})
    assert value.stopping is True


def test_desktop_pipe_close_shuts_down_and_closes_child():
    value = DesktopPipe()
    child = Child()
    value.child = child
    value.close()
    assert value.stopping is True
    assert child.stdin.closed


def test_desktop_environment_is_private_and_runtime_scoped(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(launch, "state_root", lambda _path, create=False: tmp_path / "state")
    monkeypatch.setattr(launch, "agent_root", lambda _path, create=False: tmp_path / "agent")
    monkeypatch.setattr(launch, "private_dir",
                        lambda path: calls.append(("private", path)) or path.mkdir(
                            parents=True, exist_ok=True, mode=0o700))
    monkeypatch.setattr(launch, "export_state_environment",
                        lambda _state, _env: calls.append(("export",)))
    monkeypatch.setattr(launch, "preserve_eda_temp_environment",
                        lambda _env: calls.append(("preserve",)))
    environment = {
        "SICO_PYTHON": sys.executable,
        "PYTHONPATH": "/bad", "QT_PLUGIN_PATH": "/bad", "LD_PRELOAD": "bad",
        "HOME": "/home",
    }
    value = launch.desktop_environment(tmp_path, "session", environment)
    assert value["SICO_PYTHON"] == launch.cad_python(environment)
    assert "PYTHONPATH" not in value and "QT_PLUGIN_PATH" not in value
    assert value["PYTHONNOUSERSITE"] == "1"
    assert value["PYTHONDONTWRITEBYTECODE"] == "1"
    assert value["TMPDIR"] == str(tmp_path / "agent/runtime/session")
    assert value["SICO_AI_ROUTER_DIAGNOSTIC_LOG"].endswith(
        "agent/sessions/session/router-diagnostic.jsonl")
    assert ("export",) in calls and ("preserve",) in calls


def test_bridge_control_routes_lifecycle_messages_without_native_confusion():
    closed = threading.Event()
    reader = SimpleNamespace(closed=closed)
    events = []
    bridge = SimpleNamespace(native_binding_event=events.append)
    control = launch.bridge_control(reader, bridge)
    control("shutdown")
    assert closed.is_set()
    control("show")
    control({"kind": "binding"})
    assert events == [{"kind": "binding"}]
    with pytest.raises(ValueError, match="Invalid"):
        control("other")


def test_launch_desktop_restores_resources_after_success_or_failure(monkeypatch):
    calls = []
    reader = SimpleNamespace(close=lambda: calls.append("reader"))
    control = SimpleNamespace(close=lambda: calls.append("control"))
    termination = SimpleNamespace(
        received=object(), restore=lambda: calls.append("restore"))
    monkeypatch.setattr(launch, "DesktopPipe", lambda: control)
    monkeypatch.setattr(launch, "StdioLines", lambda *_args, **_kwargs: reader)
    monkeypatch.setattr(launch, "TerminationSignal",
                        lambda: SimpleNamespace(install=lambda: termination))
    monkeypatch.setattr(launch, "_launch_desktop",
                        lambda *args: calls.append("launch") or 7)
    assert launch.launch_desktop(SimpleNamespace()) == 7
    assert calls == ["launch", "restore", "control", "reader"]

    calls.clear()
    monkeypatch.setattr(launch, "_launch_desktop",
                        lambda *args: (_ for _ in ()).throw(RuntimeError("bad")))
    with pytest.raises(RuntimeError, match="bad"):
        launch.launch_desktop(SimpleNamespace())
    assert calls[-3:] == ["restore", "control", "reader"]


def test_launch_desktop_rejects_a_foreign_directory(monkeypatch):
    context = BoundContext("instance", "generation", "target", {"cwd": "/other"})
    reader = SimpleNamespace(next=lambda: json.dumps({"context": context.record()}))
    monkeypatch.setattr(launch.os.path, "samefile", lambda *_args: False)
    with pytest.raises(ValueError, match="disagree"):
        launch._launch_desktop(SimpleNamespace(
            session="session", launch_dir="/project"), reader,
            SimpleNamespace(), object())


def test_launch_desktop_owns_discovered_bridge(monkeypatch):
    context = BoundContext("instance", "generation", "target", {"cwd": "/project"})
    reader = SimpleNamespace(next=lambda: json.dumps({"context": context.record()}))
    closed = []
    bridge = SimpleNamespace(
        descriptor={"bridge": 1}, register_target=lambda _context: None,
        close=lambda: closed.append(True))
    monkeypatch.setattr(launch.os.path, "samefile", lambda *_args: True)
    monkeypatch.setattr(launch.BridgeClient, "discover",
                        staticmethod(lambda *_args: bridge))
    monkeypatch.setattr(launch, "_run_desktop_child",
                        lambda args, *rest: "child-result")
    assert launch._launch_desktop(SimpleNamespace(
        session="", launch_dir="/project"), reader,
        SimpleNamespace(), object()) == "child-result"
    assert closed == [True]


def test_stop_project_service_on_host_exit_is_bounded(monkeypatch):
    events = []
    service = SimpleNamespace(service_id="service")

    class Stop:
        def __init__(self, *_args):
            self.ready = SimpleNamespace(result=self.result)
            self.closed = False

        def result(self, *_args):
            events.append("ready")

        def close(self):
            self.closed = True
            events.append("close")

    monkeypatch.setattr("sico.service.service_discovery.discover_project",
                        lambda _project: SimpleNamespace(
                            state="local_candidate", service=service))
    monkeypatch.setattr("sico.service.service_force_stop.ForceServiceStop", Stop)
    launch.stop_project_service_on_host_exit("/project")
    assert events == ["ready", "close"]
    monkeypatch.setattr("sico.service.service_discovery.discover_project",
                        lambda _project: SimpleNamespace(
                            state="inactive", service=None))
    launch.stop_project_service_on_host_exit("/project")
