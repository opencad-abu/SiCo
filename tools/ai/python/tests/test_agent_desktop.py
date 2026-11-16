from __future__ import annotations

import json
import os
import select
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from agent_service_launch_fixtures import stop_service
from sico.core.contracts import BoundContext
from sico.service.desktop import configured_provider_path, resume_context
from sico.service.launch import desktop_environment
from sico.service.service_discovery import discover_project
from sico.storage.history import SessionReader
from sico.storage.journal import SessionJournal
from sico.storage.session_snapshot import read as read_snapshot
from sico.transport.bridge_client import BridgeClient

ENTRY = Path(__file__).absolute().parents[3] / "sico/python/sico-entry"


@pytest.fixture
def instance_bridge_process(tmp_path):
    processes = []

    def start(context, environment):
        process = subprocess.Popen([sys.executable, "-s", str(ENTRY), "bridge", "--launch-dir",
                                    str(tmp_path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, env=environment, bufsize=0)
        processes.append(process)
        process.stdin.write((json.dumps({"context": context.record()}) + "\n").encode())
        requests = []

        def respond():
            for line in iter(process.stdout.readline, b""):
                if line.startswith(b"@"):
                    continue
                request_id = line.decode().split()[0]
                requests.append(line.decode().strip())
                process.stdin.write((json.dumps({"id": request_id, "ok": True,
                                                "result": context.snapshot}) + "\n").encode())
        threading.Thread(target=respond, daemon=True).start()
        with BridgeClient.discover(tmp_path, context) as client:
            client.context(context.instance_id)
        return process, requests

    yield start
    # Early GUI cancellation can precede service publication; reap only this test's project.
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        found = discover_project(tmp_path)
        if found.service is not None:
            stop_service(found.service)
            break
        time.sleep(0.02)
    for process in processes:
        if process.poll() is None:
            process.stdin.close()
        assert process.wait(timeout=8) == 0, process.stderr.read().decode()


def test_resume_context_prefers_a_registered_historical_target(tmp_path):
    saved = BoundContext(
        "instance", "generation", "old-target", {"cwd": str(tmp_path), "valid": True}
    )
    current = BoundContext(
        "instance", "generation", "bound", {"cwd": str(tmp_path), "valid": True}
    )

    class Broker:
        def context(self, instance_id, target_id, timeout):
            assert (instance_id, target_id, timeout) == ("instance", "old-target", 0)
            return saved

    assert resume_context(Broker(), current, saved) == saved


def read_line(process, timeout=10):
    assert select.select([process.stdout], [], [], timeout)[0], "Desktop notice timed out"
    line = process.stdout.readline()
    assert line, process.stderr.read().decode()
    return line


def test_bridge_sigterm_closes_and_seals_generation(tmp_path, instance_bridge_process):
    env = {k: v for k, v in os.environ.items() if not k.startswith("SICO_")}
    env.update(SICO_PYTHON=sys.executable, QT_QPA_PLATFORM="offscreen")
    bound = BoundContext(
        "instance", "generation", "bound", {"cwd": str(tmp_path), "valid": True}
    )
    process, _requests = instance_bridge_process(bound, env)
    process.send_signal(signal.SIGTERM)
    assert process.wait(timeout=8) == 0, process.stderr.read().decode()
    retired = list((tmp_path / ".sico/ai/agent/bridges").glob("*.retired.json"))
    assert len(retired) == 1


@pytest.mark.parametrize(
    "shutdown", ["command", "eof", "early_command", "early_eof", "signal_group"]
)
@pytest.mark.parametrize("protocol", ["mock", "anthropic", "openai"])
def test_launcher_owns_gui_child_and_restores_same_session(tmp_path, shutdown, protocol,
                                                          instance_bridge_process):
    env = {k: v for k, v in os.environ.items() if not k.startswith("SICO_")}
    env.update(SICO_PYTHON=sys.executable, QT_QPA_PLATFORM="offscreen")
    if protocol != "mock":
        env.update(
            SICO_BACKEND="python",
            SICO_PROVIDER=protocol,
            SICO_API_URL="https://gateway.invalid/v1",
            SICO_MODEL="desktop-model",
            SICO_API_KEY="desktop-test-secret",
        )
    process = subprocess.Popen(
        [
            sys.executable,
            "-s",
            str(ENTRY),
            "launch",
            "--launch-dir",
            str(tmp_path),
            "--session",
            "desktop",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        bufsize=0,
        start_new_session=shutdown == "signal_group",
    )
    try:
        bound = BoundContext(
            "instance",
            "generation",
            "bound",
            {
                "cwd": str(tmp_path),
                "valid": True,
                "cellview": {"lib": "lib", "cell": "cell", "view_name": "schematic"},
            },
        )
        instance_bridge_process(bound, env)
        process.stdin.write((json.dumps({"context": bound.record()}) + "\n").encode())
        if not shutdown.startswith("early_"):

            def notice():
                line = read_line(process)
                assert line.startswith(b"@"), "Desktop launcher must never be a SKILL relay"
                return line

            shown = notice()
            assert shown == b"@shown desktop\n"
            process.stdin.write(b"@show\n")
            assert notice() == shown
        if shutdown == "signal_group":
            os.killpg(process.pid, signal.SIGTERM)
        elif shutdown.endswith("command"):
            process.stdin.write(b"@shutdown\n")
        else:
            process.stdin.close()
        assert process.wait(timeout=8) == 0, process.stderr.read().decode()
        log = tmp_path / ".sico/ai/agent/runtime/desktop/desktop.log"
        assert log.is_file() and log.stat().st_mode & 0o777 == 0o600
        assert "Traceback" not in log.read_text()
        # GUI exit leaves the service writer alive. Inspect only persisted history.
        root = tmp_path / ".sico/ai/agent"
        if shutdown.startswith("early_"):
            # Cancellation may precede durable creation; it must not manufacture a session.
            return
        snapshot = read_snapshot(root / "sessions/desktop")
        assert snapshot["context"] == bound.record()
        events = SessionReader(root, "desktop").events()
        with pytest.raises(RuntimeError, match="active writer"):
            SessionJournal(tmp_path, "desktop")
        assert [e["sequence"] for e in events] == list(range(1, len(events) + 1))
        starts = [e for e in events if e["kind"] == "task.started"]
        assert len(starts) <= 1
        assert all(e["payload"]["origin"] == "startup" for e in starts)
        model_events = [e for e in events if e["kind"].startswith("model.")]
        assert all(e["kind"] == "model.status" and e["payload"]["phase"] == "context"
                   for e in model_events)
    finally:
        if process.poll() is None:
            process.stdin.close()
            process.wait(timeout=40)


def test_reopened_desktop_keeps_service_sessions_and_bridge(tmp_path, instance_bridge_process):
    """A new launch reuses the service session that the closed window left detached."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("SICO_")}
    env.update(SICO_PYTHON=sys.executable, QT_QPA_PLATFORM="offscreen")
    initial = BoundContext("instance", "generation", "bound",
                           {"cwd": str(tmp_path), "valid": True})
    bridge_process, requests = instance_bridge_process(initial, env)
    children = []
    original = None
    try:
        for session_id in ("desktop-a", "desktop-b"):
            context = BoundContext(initial.instance_id, initial.generation, session_id,
                                   initial.snapshot)
            process = subprocess.Popen([sys.executable, "-s", str(ENTRY), "launch", "--launch-dir",
                                        str(tmp_path), "--session", session_id],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, env=env, bufsize=0)
            children.append(process)
            process.stdin.write((json.dumps({"context": context.record()}) + "\n").encode())
            # The second launch asks for desktop-b but the service continues desktop-a.
            assert read_line(process) == b"@shown desktop-a\n"
            if session_id == "desktop-a":
                with BridgeClient.discover(tmp_path, initial) as client:
                    original = client.router_snapshot(initial)["router_id"]
                # A project has one window; its service session survives window closure.
                process.stdin.write(b"@shutdown\n")
                assert process.wait(timeout=8) == 0, process.stderr.read().decode()
                assert bridge_process.poll() is None
        with BridgeClient.discover(tmp_path, initial) as client:
            snapshot = client.bridge_snapshot()
            assert set(snapshot["sessions"]) == {"desktop-a"}
            assert len(snapshot["instances"]) == 1
            assert snapshot["instances"][0]["router_id"] == original
            assert bridge_process.poll() is None
            # Closing the first GUI does not release its service-owned session.
            assert "desktop-a" in client.bridge_snapshot()["sessions"]
            assert client.router_snapshot(initial)["router_id"] == original
            context = client.context("instance", "desktop-b")
            client.register_session("observer", context)
            assert client.get_context(context)["valid"]
            assert any(line.split()[1:3] == ["get_context", "desktop-b"] for line in requests)
            children[1].stdin.write(b"@show\n")
            assert read_line(children[1]) == b"@shown desktop-a\n"
    finally:
        for process in children:
            if process.poll() is None:
                process.stdin.write(b"@shutdown\n")
                assert process.wait(timeout=8) == 0, process.stderr.read().decode()


def test_python_environment_and_provider_path(tmp_path):
    with SessionJournal(tmp_path, "s"):
        env = desktop_environment(
            tmp_path,
            "s",
            {
                "SICO_PYTHON": sys.executable,
                "PYTHONPATH": "/virtuoso",
                "QT_PLUGIN_PATH": "/eda/qt",
                "QT_XCB_NO_XI2": "1",
                "QT_XCB_NO_XI2_MOUSE": "1",
                "DISPLAY": ":2",
                "LOADEDMODULES": "IC231",
                "SICO_API_KEY": "secret",
                "SICO_API_URL": "https://gateway.example/v1",
                "SICO_MODEL": "backend-model",
                "SICO_PROVIDER": "openai",
            },
        )
    assert "PYTHONPATH" not in env and "QT_PLUGIN_PATH" not in env
    assert "QT_XCB_NO_XI2" not in env and "QT_XCB_NO_XI2_MOUSE" not in env
    assert env["DISPLAY"] == ":2" and env["LOADEDMODULES"] == "IC231"
    assert env["SICO_API_KEY"] == "secret"
    assert env["SICO_API_URL"] == "https://gateway.example/v1"
    assert env["SICO_MODEL"] == "backend-model" and env["SICO_PROVIDER"] == "openai"
    assert Path(env["TMPDIR"]).is_relative_to(tmp_path / ".sico/ai")
    assert Path(env["XDG_RUNTIME_DIR"]).stat().st_mode & 0o777 == 0o700
    path = tmp_path / ".sico/ai/agent-provider.json"
    assert configured_provider_path(tmp_path, environment={}) is None
    path.write_text("{}")
    assert configured_provider_path(tmp_path, environment={}) == path
    assert (
        configured_provider_path(tmp_path, environment={"SICO_PROVIDER_CONFIG": "model.json"})
        == tmp_path / "model.json"
    )
