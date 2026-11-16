"""A real GUI client cannot own execution or terminate it through window shutdown."""

import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from agent_boundary_guard import product_errors, window_errors
from agent_gui_wait import wait_until
from agent_service_boundary_fixtures import forbid_gui_owners, isolated_service
from agent_service_gui_fixtures import ServiceTestWindow as AssistantWindow
from agent_service_gui_fixtures import startup_args
from agent_thread_guard import ThreadGuard
from agent_worker_fixtures import heartbeat
from PyQt5.QtWidgets import QApplication

from sico.service.frontend_startup import FrontendStartup
from sico.service.remote_frontend import RemoteFrontend
from sico.transport.framing import Connection
from sico_ui.startup import StartupView


def test_window_detach_cannot_own_or_stop_an_accepted_task(tmp_path, monkeypatch):
    with isolated_service(tmp_path) as service:
        calls = forbid_gui_owners(monkeypatch)
        guard = ThreadGuard(monkeypatch)
        guard.protect(Connection, "send")
        guard.protect(Connection, "receive")
        startup = FrontendStartup(startup_args(tmp_path), lambda *a, **kw: None, environment={})
        window = None
        observer = None
        try:
            wait_until(startup.ready.done)
            window = AssistantWindow(startup.ready.result().frontend)
            window.show()
            wait_until(window.steering_input.available)
            assert not product_errors(window.api)
            assert not window_errors(window)
            assert not any(hasattr(startup, name)
                           for name in ("_journal", "_provider", "_sessions"))
            window.input.setPlainText("durably accepted across GUI detach")
            window.actions.submit()
            wait_until(service.entered.is_set)
            wait_until(lambda: not window.input.toPlainText())
            assert service.evidence.poll(3)
            pid, prompt = service.evidence.recv()
            assert pid == service.process.pid != os.getpid()
            assert "durably accepted across GUI detach" in prompt
            heartbeat(window)
            assert not service.release.is_set()
            token = window.page.session
            window.request_quit()
            wait_until(lambda: window.api.wait_desktop(0))
            assert window.api.detached and service.process.is_alive()
            assert not service.release.is_set() and not calls
            observer = RemoteFrontend(service.descriptor, tmp_path, token.session_id)
            attached = observer.open_session(token.session_id).result(5)
            assert attached == token and not observer.can_control(attached)
            prepared = observer.prepare_replay(attached, 1, 200).result(5)
            kinds = []
            def read_events():
                receipt = observer.event_batch(prepared.stream)
                batch = receipt.result(5)
                prepared.stream.received(receipt)
                assert not batch.error
                kinds.extend(row["kind"] for row in batch.events)
                return batch.end == batch.committed
            wait_until(read_events)
            assert "task.started" in kinds and "task.completed" not in kinds
            service.release.set()
            wait_until(lambda: (read_events(), "task.completed" in kinds)[1])
            assert service.process.is_alive() and not calls
            assert {name for name, _ in guard.calls} == {"send", "receive"}
        finally:
            service.release.set()
            if observer:
                observer.close_desktop()
                wait_until(lambda: observer.wait_desktop(0))
            if window:
                window.request_quit()
                window.deleteLater()
            startup.close()
            assert startup.wait(5)


@pytest.mark.parametrize("cancel", [False, True])
def test_paused_discovery_keeps_qt_responsive_without_client_owned_service(
        tmp_path, monkeypatch, cancel):
    from sico.service import service_startup

    with isolated_service(tmp_path) as service:
        calls = forbid_gui_owners(monkeypatch)
        entered, release = threading.Event(), threading.Event()
        original = service_startup.discover_project
        def paused(project):
            entered.set()
            assert release.wait(10)
            return original(project)
        monkeypatch.setattr(service_startup, "discover_project", paused)
        startup = FrontendStartup(startup_args(tmp_path), lambda *a, **kw: None, environment={})
        view = StartupView(startup, None, None)
        try:
            wait_until(entered.is_set)
            heartbeat(view.progress)
            assert not startup.ready.done() and not release.is_set()
            if cancel:
                view.close()
                QApplication.instance().processEvents()
                assert service.process.is_alive() and not release.is_set()
            release.set()
            if cancel:
                assert startup.wait(5) and view.window is None
            else:
                wait_until(lambda: view.window is not None)
                assert not product_errors(view.window.api)
            assert not calls and service.process.is_alive()
        finally:
            release.set()
            view.close()
            assert startup.wait(5)


def test_real_qt_application_exit_only_detaches_its_service(tmp_path):
    code = '''
import sys
from pathlib import Path
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication
from agent_service_boundary_fixtures import forbid_gui_owners
from agent_service_gui_fixtures import startup_args
from sico.service.frontend_startup import FrontendStartup
from sico_ui.window import AssistantWindow
from pytest import MonkeyPatch

app = QApplication([])
app.setQuitOnLastWindowClosed(False)
patches = MonkeyPatch()
calls = forbid_gui_owners(patches)
startup = FrontendStartup(startup_args(Path(sys.argv[1])), lambda *a, **kw: None, environment={})
windows, submitted = [], []
def tick():
    if not windows and startup.ready.done():
        window = AssistantWindow(startup.ready.result().frontend)
        window.show()
        windows.append(window)
    if not windows:
        return
    window = windows[0]
    if not submitted and window.steering_input.available():
        window.input.setPlainText('real GUI process accepted task')
        window.actions.submit()
        submitted.append(True)
    if submitted and not window.input.toPlainText() and window.presentation.state.get('busy'):
        timer.stop()
        window.request_quit()
timer = QTimer()
timer.timeout.connect(tick)
timer.start(10)
app.exec_()
assert submitted and windows[0].api.detached and not calls
startup.close()
assert startup.wait(5)
patches.undo()
print('GUI exited; execution remains service-owned', flush=True)
'''
    with isolated_service(tmp_path) as service:
        environment = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONPATH=os.pathsep.join(
            [str(Path(__file__).parent), *sys.path]))
        child = subprocess.Popen([sys.executable, "-c", code, str(tmp_path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=environment)
        try:
            assert service.entered.wait(10)
            output, error = child.communicate(timeout=10)
            assert child.returncode == 0, error
            assert "GUI exited" in output
            assert not service.release.is_set() and service.process.is_alive()
            assert service.evidence.poll(2)
            assert service.evidence.recv()[0] == service.process.pid
        finally:
            service.release.set()
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=3)
