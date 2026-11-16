"""Exercise real CLI entry and process exit around the first Qt event loop."""

import json
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest
from agent_service_launch_fixtures import stop_service

import sico.cli as cli
import sico.service.local_startup as local_module


@pytest.fixture(autouse=True)
def release_test_project_service(tmp_path):
    from sico.service.service_discovery import discover_project

    yield
    found = discover_project(tmp_path)
    if found.service is not None:
        stop_service(found.service)


@pytest.mark.parametrize("command", ["demo", "connect"])
def test_gui_cli_routes_before_synchronous_backend_dependencies(monkeypatch, command):
    called = []

    def forbidden(*args, **kwargs):
        pytest.fail("GUI entry constructed a synchronous CLI dependency")

    def run(args):
        called.append(args)
        return 17

    for name in ("load_provider", "SessionJournal", "ContextBroker", "DemoPeer", "create_backend"):
        monkeypatch.setattr(cli, name, forbidden)
    monkeypatch.setattr(local_module, "run_local_gui", run)
    assert cli.main([command, "--gui", "--message", "draft"]) == 17
    assert len(called) == 1 and called[0].command == command and called[0].message == "draft"


def environment():
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("SICO_", "CADAI_"))}
    env.update(QT_QPA_PLATFORM="offscreen", PYTHONPATH=os.pathsep.join(sys.path))
    return env


@pytest.fixture
def service_cleanup(tmp_path):
    """Retire only the independent service created in this test's private project."""
    from sico.service.background_watchdog import process_identity

    descriptor = tmp_path / ".cad/ai/agent/service/discovery.json"
    assert not descriptor.exists()
    yield
    if not descriptor.exists():
        return  # Cancellation before service discovery, or the in-process launcher.
    row = json.loads(descriptor.read_text())

    def alive():
        try:
            actual = process_identity(row["pid"])
        except (FileNotFoundError, ProcessLookupError):
            return False
        return actual["start"] == row["process_start"] and actual["state"] not in {"Z", "X"}

    if not alive():
        return
    command = Path(f'/proc/{row["pid"]}/cmdline').read_bytes().split(b"\0")
    assert b"project-service" in command and os.fsencode(tmp_path) in command
    os.kill(row["pid"], signal.SIGTERM)
    deadline = time.monotonic() + 5
    while alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    if alive():
        os.kill(row["pid"], signal.SIGKILL)
        pytest.fail("Test project service did not stop after SIGTERM")


@pytest.mark.parametrize("command", ["demo", "connect"])
@pytest.mark.parametrize("stop", ["ready", "cancel", "drained"])
def test_gui_cli_worker_starts_inside_event_loop_and_process_exits(
        tmp_path, service_cleanup, command, stop):
    script = textwrap.dedent(r'''
        import json, queue, re, shlex, sys, threading
        from pathlib import Path
        from types import SimpleNamespace
        from PyQt5.QtCore import QTimer
        from PyQt5.QtWidgets import QApplication
        import sico.providers.config as provider_config
        command, stop, root = sys.argv[1:]
        app = QApplication([])
        qt_owner = threading.get_ident()
        active, entered, release = threading.Event(), threading.Event(), threading.Event()
        ticks, relay_errors, drained = [], [], []
        relay_input = queue.Queue(maxsize=1)
        relay_stop, relay_ready = threading.Event(), threading.Event()
        original = provider_config.resolve_provider_config
        def config(*args, **kwargs):
            assert active.is_set() and threading.get_ident() != qt_owner
            entered.set()
            assert release.wait(5)
            return original(*args, **kwargs)
        # Install at the owner before either startup module captures its import.
        provider_config.resolve_provider_config = config
        from sico.cli import main
        from sico.demo import DemoPeer
        from sico.core.contracts import BoundContext
        from sico_ui.startup import StartupDialog
        from sico_ui.window import AssistantWindow

        def relay():
            peer = None
            try:
                while not relay_stop.is_set():
                    try:
                        text = relay_input.get(timeout=0.05)
                        break
                    except queue.Empty:
                        continue
                else:
                    return
                match = re.search(
                    r'sicoConnect\(("(?:[^"\\]|\\.)*")\s+"([0-9a-f]{32})"'
                    r'\s+"([0-9a-f]{32})"\)', text)
                assert match, text
                args = shlex.split(json.loads(match.group(1)))
                path = Path(args[args.index("--connection-file") + 1])
                assert path.is_relative_to(Path(root))
                descriptor = json.loads(path.read_text())
                endpoint = SimpleNamespace(address=(descriptor["host"], descriptor["port"]),
                                           token=descriptor["token"])
                context = BoundContext(match.group(2), match.group(3), "bound",
                                       {"source": "simulated", "valid": True, "cwd": root})
                peer = DemoPeer(endpoint, context, cancelled=relay_stop.is_set)
                relay_ready.set()
                relay_stop.wait()
            except BaseException as exc:
                relay_errors.append(repr(exc))
            finally:
                if peer is not None:
                    peer.close()

        relay_thread = threading.Thread(target=relay, name="test-relay", daemon=True)
        if command == "connect":
            relay_thread.start()
        QTimer.singleShot(0, active.set)
        timer = QTimer()
        instructions_sent = []
        def poll():
            assert threading.get_ident() == qt_owner
            if not entered.is_set():
                return
            progress = next((w for w in app.topLevelWidgets()
                             if isinstance(w, StartupDialog) and w.isVisible()), None)
            if not release.is_set() and progress is not None:
                ticks.append(True)
                if len(ticks) < 3:
                    return
                if stop == "cancel":
                    progress.cancel.click()
                release.set()
            if command == "connect" and progress is not None and not instructions_sent:
                text = progress.instructions.toPlainText()
                if text:
                    relay_input.put_nowait(text)
                    instructions_sent.append(True)
            if stop in {"ready", "drained"}:
                window = next((w for w in app.topLevelWidgets()
                               if isinstance(w, AssistantWindow) and w.isVisible()), None)
                if window is not None and not window.page.opening and not window.api.desktop_busy:
                    assert window.input.toPlainText() == "unsent draft"
                    if stop == "drained" and not drained:
                        window.binding.timer.stop()
                        window.lifecycle.request()
                        window.clear_data_receipts()
                        window.binding.dispose()
                        drained.append(window.api)
                    if drained:
                        if not window.api.wait_desktop(0):
                            return
                        assert not window.api.desktop_busy
                    timer.stop()
                    if stop == "ready":
                        window.request_quit()
                    else:
                        window.close()
        timer.timeout.connect(poll)
        timer.start(5)
        try:
            result = main([command, "--gui", "--launch-dir", root, "--session", "local-cli",
                           "--message", "unsent draft", "--timeout", "3"])
        finally:
            timer.stop()
            release.set()
            relay_stop.set()
            if command == "connect":
                relay_thread.join(2)
                assert not relay_thread.is_alive()
        assert len(ticks) >= 3 and release.is_set()
        assert not relay_errors, relay_errors
        assert relay_ready.is_set() == (command == "connect" and stop != "cancel")
        if stop == "drained":
            assert drained and drained[0].wait_desktop(0)
        assert not any(t.name == "copilot-startup" and t.is_alive()
                       for t in threading.enumerate())
        raise SystemExit(result)
    ''')
    result = subprocess.run([sys.executable, "-c", script, command, stop, str(tmp_path)],
                            capture_output=True, env=environment(), timeout=10)
    assert result.returncode == 0, result.stderr.decode(errors="replace")


def test_non_gui_demo_retains_execution_and_history_semantics(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "sico.cli", "demo", "--launch-dir", str(tmp_path),
         "--session", "cli", "--message", "Read this context"],
        capture_output=True, env=environment(), timeout=10,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    events = [json.loads(line) for line in result.stdout.decode().splitlines()
              if line.startswith("{")]
    assert events[-1]["kind"] == "task.completed"
    started = next(event for event in events if event["kind"] == "task.started")
    assert started["payload"]["text"] == "Read this context"
    assert started["payload"]["context"]["snapshot"]["source"] == "simulated"
    history = subprocess.run(
        [sys.executable, "-m", "sico.cli", "history", "--launch-dir", str(tmp_path),
         "--session", "cli"], capture_output=True, env=environment(), timeout=5,
    )
    assert history.returncode == 0, history.stderr.decode(errors="replace")
    recorded = [json.loads(line) for line in history.stdout.decode().splitlines()]
    assert [event for event in recorded if event["kind"] != "router.status"] == events
    receipts = [event for event in recorded if event["kind"] == "router.status"]
    assert receipts and all(event["payload"]["session_id"] == "cli" for event in receipts)
