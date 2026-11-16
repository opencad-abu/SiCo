# Original test bodies retain their imports and Qt lifetime references.
# ruff: noqa: F401, F841, I001
"""agent gui contract lifecycle contracts."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time

from agent_window_fixture import create_window as AssistantWindow

from sico.core.contracts import BoundContext, ModelTurn
from sico.service.python_backend import PythonBackend
from sico.core.tools import ToolRegistry
from sico.storage.journal import SessionJournal


def test_closing_desktop_forces_exit_when_a_service_still_reports_busy(tmp_path, monkeypatch):
    """A session-ended shutdown must not leave a zombie window blocking the bridge."""

    from sico_ui.session_binding import SessionBinding

    class Provider:
        label = "stub"

        def stream(self, system, messages, tools, cancelled):
            yield ModelTurn("done")

    with SessionJournal(tmp_path, "quit") as journal:
        loop = PythonBackend(Provider(), ToolRegistry(), journal,
                         BoundContext("i", "g", "bound", {"valid": True}))
        window = AssistantWindow(loop)
        try:
            monkeypatch.setattr(type(window.sessions), "busy", property(lambda self: True))
            finished = []
            monkeypatch.setattr(window.binding, "finish_close", lambda: finished.append(True))
            window.request_quit()
            monkeypatch.setattr(type(window.lifecycle), "GRACE_SECONDS", 60.0)
            window.binding.poll()
            assert finished == []  # work still reported: the window keeps its grace
            window.lifecycle.deadline = time.monotonic() - 1
            window.binding.poll()
            assert finished == [True]  # past the grace the exit is forced
        finally:
            window.close()
            window.sessions.wait(5)
