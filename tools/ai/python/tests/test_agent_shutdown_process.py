"""Explicit shutdown bounds Qt, service cleanup and interpreter exit together."""

import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest


@pytest.mark.parametrize("scenario", ["busy", "qt_blocked", "interpreter", "stderr_full",
                                      "startup_eof", "normal"])
def test_desktop_shutdown_process_deadline(tmp_path, scenario):
    script = textwrap.dedent('''
        import os, sys, threading, time
        from types import SimpleNamespace
        from PyQt5.QtCore import QTimer
        from PyQt5.QtWidgets import QApplication
        from sico.service.desktop import DesktopShutdown, run_desktop
        scenario, directory = sys.argv[1:]
        if scenario == "startup_eof":
            raise SystemExit(run_desktop(SimpleNamespace(launch_dir=directory, session="startup")))
        guard = DesktopShutdown()
        # Most paths use a short deadline; exercise the production five-second
        # setting once, including the non-daemon interpreter shutdown wait.
        if scenario != "interpreter":
            guard.GRACE_SECONDS = 0.4
        if scenario in {"interpreter", "stderr_full"}:
            if scenario == "stderr_full":
                reader, writer = os.pipe()
                os.set_blocking(writer, False)
                try:
                    while True: os.write(writer, b"x" * 4096)
                except BlockingIOError: pass
                os.set_blocking(writer, True)
                os.dup2(writer, 2)
            threading.Thread(target=lambda: threading.Event().wait(30)).start()
            guard.arm()
            raise SystemExit(0)
        from sico.core.contracts import BoundContext
        from sico.service.python_backend import PythonBackend
        from sico.core.tools import ToolRegistry
        from sico.providers.mock import ScriptedProvider
        from sico.service.controller import SessionController
        from agent_frontend_harness import frontend
        from agent_sessions_harness import DesktopSessions
        from sico.storage.journal import SessionJournal
        from sico_ui.window import AssistantWindow
        app = QApplication([])
        app.setQuitOnLastWindowClosed(False)
        with SessionJournal(directory, "shutdown") as journal:
            sessions = DesktopSessions(SessionController(PythonBackend(
                ScriptedProvider([]), ToolRegistry(), journal, BoundContext("i", "g", "target", {}))))
            window = AssistantWindow(frontend(sessions.worker))
            window.lifecycle.guard = guard
            if scenario == "busy":
                type(sessions).busy = property(lambda self: True)
            if scenario == "qt_blocked":
                sessions.close = lambda: threading.Event().wait(30)
            QTimer.singleShot(0, window.request_quit)
            app.exec_()
            if not sessions.wait(1): raise SystemExit(3)
    ''')
    environment = dict(os.environ, QT_QPA_PLATFORM="offscreen",
                       PYTHONPATH=os.pathsep.join([*sys.path, str(Path(__file__).absolute().parent)]))
    start = time.monotonic()
    result = subprocess.run([sys.executable, "-c", script, scenario, str(tmp_path)],
                            stdin=subprocess.DEVNULL, capture_output=True, env=environment,
                            timeout=9)
    elapsed = time.monotonic() - start
    expected = 0 if scenario in {"startup_eof", "normal"} else 2
    assert result.returncode == expected, result.stderr.decode(errors="replace")
    if scenario == "interpreter":
        assert 5 <= elapsed < 7
    else:
        assert elapsed < 4


def test_desktop_sigterm_uses_bounded_parent_exit_shutdown(tmp_path):
    marker = tmp_path / "signal-installed"
    script = textwrap.dedent('''
        import sys
        from pathlib import Path
        from types import SimpleNamespace
        from sico.service import desktop
        marker = Path(sys.argv[2])
        class Marked(desktop.TerminationSignal):
            def install(self):
                result = super().install()
                marker.write_text("installed")
                return result
        desktop.TerminationSignal = Marked
        raise SystemExit(desktop.run_desktop(
            SimpleNamespace(launch_dir=sys.argv[1], session="sigterm")))
    ''')
    environment = dict(os.environ, QT_QPA_PLATFORM="offscreen",
                       PYTHONPATH=os.pathsep.join([*sys.path, str(Path(__file__).absolute().parent)]))
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(tmp_path), str(marker)],
        cwd=tmp_path, env=environment, stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 10
        while not marker.exists():
            assert process.poll() is None and time.monotonic() < deadline
            time.sleep(0.05)
        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=5) == 0, process.stderr.read().decode(errors="replace")
        assert b"Desktop shutdown deadline expired" not in process.stderr.read()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
