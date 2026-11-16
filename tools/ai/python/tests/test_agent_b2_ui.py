"""Qt receipts must remain scoped while background jobs run independently."""
import os
import threading
import time
from concurrent.futures import Future
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from agent_gui_wait import wait_until
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication, QWidget
from test_agent_b1_worker import runtime as runtime
from test_agent_b2_jobs import TARGET, origin


class Service:
    def __init__(self):
        self.calls = []

    def request(self, method, *args, **kwargs):
        f = Future()
        self.calls.append((method, args, kwargs, f))
        return f


def host():
    window = QWidget()
    window.page, window.presentation, window.lifecycle = (SimpleNamespace() for _ in range(3))
    window.controller = SimpleNamespace(session_id='s', runtime_id='r')
    window.sessions = SimpleNamespace(background=Service(), close=lambda: None, wait=lambda *_: True)
    window.page.session = window.controller
    window.api = SimpleNamespace(background_request=lambda handle, method, *args, **kwargs:
        window.sessions.background.request(method, *args, session_id=handle.session_id, **kwargs))
    window.page.activation = 1
    window.page.reviewing = None
    window.lifecycle.closing = False
    window.page.opening = False
    window.presentation.submission_context = origin()
    return window


def test_submit_and_late_receipts_do_not_block_or_cross_sessions(tmp_path):
    from sico_ui.background import BackgroundPanel
    app = QApplication.instance() or QApplication([])
    window = host()
    panel = BackgroundPanel(window)
    panel.resize(850, 600)
    panel.show()
    try:
        panel.sync()
        window.sessions.background.calls[-1][3].set_result([])
        wait_until(lambda: panel.poll_receipt.pending is None)
        panel.submit()
        request = window.sessions.background.calls[-1]
        assert request[0] == 'submit_background'
        assert request[1] == ('inspect_schematic', TARGET)
        assert request[2]['origin'] == origin()
        ticks = []
        timer = QTimer(panel)
        timer.timeout.connect(lambda: ticks.append(1))
        timer.start(10)
        wait_until(lambda: len(ticks) >= 3)
        timer.stop()
        assert not request[3].done()
        accepted = dict(job_id='a' * 32, worker_id='b' * 32, owner_session_id='s',
                        state='accepted', target=TARGET, accepted_at=time.time())
        request[3].set_result(accepted)
        wait_until(lambda: panel.command_receipt.pending is None)
        assert panel.jobs.topLevelItem(0).text(1) == '已接收'
        panel.poll()
        old = window.sessions.background.calls[-1][3]
        old.set_running_or_notify_cancel()
        window.controller = SimpleNamespace(session_id='other', runtime_id='other-runtime')
        window.page.session = window.controller
        panel.sync()
        old.set_result([dict(accepted, state='completed')])
        app.processEvents()
        assert panel.jobs.topLevelItemCount() == 0
        assert not panel.result.toPlainText()
        window.page.reviewing = 'history'
        panel.sync()
        assert not panel.submit_button.isEnabled()
        assert panel.grab().save(str(tmp_path / 'background-panel.png'))
    finally:
        panel.close()
        window.close()


def test_result_requires_verified_service_response_and_stale_selection_is_ignored():
    from sico_ui.background import BackgroundPanel
    app = QApplication.instance() or QApplication([])
    window = host()
    panel = BackgroundPanel(window)
    try:
        panel.sync()
        ref = dict(path='artifacts/result.json', size=100, sha256='c' * 64)
        row = dict(job_id='a' * 32, worker_id='b' * 32, state='completed', target=TARGET,
                   artifact=ref, request_id='d' * 32, finished_at=time.time())
        window.sessions.background.calls[-1][3].set_result([row])
        wait_until(lambda: panel.poll_receipt.pending is None)
        panel.jobs.setCurrentItem(panel.jobs.topLevelItem(0))
        panel.read_result()
        request = window.sessions.background.calls[-1]
        assert request[0] == 'read_background_result' and request[1] == (row['job_id'], ref)
        request[3].set_exception(ValueError('checksum mismatch'))
        wait_until(lambda: panel.result_receipt.pending is None)
        assert 'checksum' in panel.notice.text()
        assert not panel.result.toPlainText()
        panel.read_result()
        request = window.sessions.background.calls[-1]
        request[3].set_running_or_notify_cancel()
        panel.render([])
        request[3].set_result({'inspection': 'old design'})
        app.processEvents()
        assert not panel.result.toPlainText()
        panel.render([row])
        panel.jobs.setCurrentItem(panel.jobs.topLevelItem(0))
        panel.result.setPlainText('previously verified result')
        panel.render([])
        assert not panel.result.toPlainText()
    finally:
        panel.close()
        window.close()


def test_real_window_runs_background_without_starting_conversation(tmp_path, runtime, monkeypatch):
    from agent_sessions_harness import DesktopSessions
    from agent_window_fixture import create_window as AssistantWindow

    from sico.service.python_backend import PythonBackend
    from sico.core.tools import ToolRegistry
    from sico.providers.mock import ScriptedProvider
    from sico.service.background_service import BackgroundService
    from sico.service.controller import SessionController
    from sico.storage.journal import SessionJournal
    from sico_ui.theme import COPILOT_STYLESHEET
    app = QApplication.instance() or QApplication([])
    (runtime.virtuoso.parent / 'mode').write_text('delay')
    gui_thread = threading.get_ident()
    from sico.service.background_worker import BackgroundRuntime
    validate = BackgroundRuntime.validate
    def off_thread(runtime):
        assert threading.get_ident() != gui_thread
        return validate(runtime)
    monkeypatch.setattr(BackgroundRuntime, 'validate', off_thread)
    with SessionJournal(tmp_path, 'b2-ui') as journal:
        loop = PythonBackend(ScriptedProvider([]), ToolRegistry(), journal, origin())
        loop._event("session.created", {"entry": "test"})
        controller = SessionController(loop)
        background = BackgroundService(journal.root / 'background', runtime=runtime,
                                       worker_options={'shutdown_timeout': .2})
        sessions = DesktopSessions(controller, background=background)
        window = AssistantWindow(controller, sessions=sessions)
        window.setStyleSheet(COPILOT_STYLESHEET)
        try:
            # Startup scans retained jobs asynchronously before lifecycle can report idle.
            wait_until(lambda: background._manager is not None)
            assert not background._manager._workers
            window.resize(1280, 800)
            window.show()
            window.show_background()
            panel = window.background_panel
            # The submit control enables only after the first session snapshot
            # arrives through the binding.
            wait_until(lambda: panel.submit_button.isEnabled())
            panel.submit()
            wait_until(lambda: panel.jobs.topLevelItemCount() == 1)
            ticks = []
            timer = QTimer(window)
            timer.timeout.connect(lambda: ticks.append(1))
            timer.start(10)
            wait_until(lambda: len(ticks) >= 3)
            assert window.input.isEnabled()
            wait_until(lambda: any(r['state'] == 'completed' for r in panel.rows.values()), timeout=8)
            timer.stop()
            panel.jobs.setCurrentItem(panel.jobs.topLevelItem(0))
            panel.read_result()
            wait_until(lambda: bool(panel.result.toPlainText()))
            result = __import__('json').loads(panel.result.toPlainText())
            assert result['owner_session_id'] == controller.session_id
            assert result['origin'] == origin().record()
            assert not result['automatic_resume_allowed']
            assert not any(e['kind'] in {'task.started', 'task.completed'} for e in journal.events())
            assert window.grab().save(str(tmp_path / 'background-window.png'))
        finally:
            sessions.close()
            assert sessions.wait(6)
            window.close()
            window.deleteLater()
            app.processEvents()
