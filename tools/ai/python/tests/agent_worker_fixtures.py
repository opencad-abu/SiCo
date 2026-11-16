"""Shared live desktop fixture and event-loop heartbeat for boundary tests."""

import os

import pytest
from agent_gui_wait import settle

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QTimer
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from sico.core.contracts import BoundContext
from sico.service.python_backend import PythonBackend
from sico.core.tools import ToolRegistry
from sico.providers.mock import ScriptedProvider
from sico.storage.journal import SessionJournal
from agent_window_fixture import create_window as AssistantWindow


@pytest.fixture
def window(tmp_path):
    app = QApplication.instance() or QApplication([])
    context = BoundContext("i", "g", "initial", {})
    with SessionJournal(tmp_path, "history") as journal:
        history = PythonBackend(ScriptedProvider([]), ToolRegistry(), journal, context)
        history._event("session.created", {})
    with SessionJournal(tmp_path, "initial") as journal:
        loop = PythonBackend(ScriptedProvider([]), ToolRegistry(), journal, context)
        loop._event("session.created", {})
        widget = AssistantWindow(loop)
        try:
            settle(widget)
            yield widget
        finally:
            widget.sessions.close()
            assert widget.sessions.wait(5)
            widget.close()
            widget.deleteLater()
            app.processEvents()


def heartbeat(window):
    ticks = []
    timer = QTimer(window)
    timer.setInterval(5)
    timer.timeout.connect(lambda: ticks.append(True))
    timer.start()
    QTest.qWait(60)
    timer.stop()
    assert len(ticks) >= 2

