"""Explicit backend setup for UI tests; production windows only receive handles."""

from agent_frontend_harness import frontend
from agent_sessions_harness import DesktopSessions

from sico.service.controller import SessionController
from sico_ui.window import AssistantWindow


class TestWindow(AssistantWindow):
    __test__ = False

    def __init__(self, frontend, sessions, **kwargs):
        self._test_sessions = sessions
        super().__init__(frontend, **kwargs)

    @property
    def controller(self):
        owner = self._test_sessions.controllers[self.page.session.session_id]
        assert owner.runtime_id == self.page.session.runtime_id
        return owner

    @property
    def sessions(self):
        return self._test_sessions

    def finish_close(self):
        # A test window must not terminate the QApplication shared by later tests.
        self.display.set_busy(False)
        self.close()


def create_window(loop, *, sessions=None, **kwargs):
    controller = loop if isinstance(loop, SessionController) else SessionController(loop)
    sessions = sessions or DesktopSessions(controller)
    return TestWindow(frontend(sessions.worker, controller), sessions, **kwargs)


def workbench_backend(window):
    source = window.centers.source
    return next(candidate for candidate in window.sessions.data._sources
                if candidate.session_id == source.session_id
                and candidate.identity == source.identity and not candidate._closed.is_set())
