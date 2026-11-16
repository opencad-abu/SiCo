# Original test bodies retain their imports and Qt lifetime references.
# ruff: noqa: F401, F841, I001
"""Shared agent navigation contract fixtures and helpers."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import agent_sessions_harness as sessions_module
import pytest
from agent_gui_wait import settle
from agent_window_fixture import create_window as AssistantWindow
from info_app_fixtures import notice_text
from PyQt5.QtCore import QEvent, Qt
from PyQt5.QtWidgets import QApplication

from sico.core.contracts import BoundContext, ModelTurn, TextDelta
from sico.service.python_backend import PythonBackend
from sico.core.tools import ToolRegistry
from sico.providers.mock import ScriptedProvider
from sico.storage.journal import SessionJournal


class DeletableLoop(PythonBackend):
    def __init__(self, provider, tools, journal, context, **_options):
        super().__init__(provider, tools, journal, context)

    def delete_thread(self):
        self._event("codex.thread.deleted", {})

    def set_thread_name(self, name):
        if not getattr(self, "thread_id", None):
            self.thread_id = self.journal.session_id
            self._event("codex.thread", {"thread_id": self.thread_id})
        self._event("codex.thread.name", {"thread_id": self.thread_id, "name": name})


class BlockingProvider:
    """First turn blocks until the test releases it; later turns complete."""

    label = "阻塞模型"

    def __init__(self, entered, release):
        self.entered, self.release = entered, release

    def stream(self, system, messages, tools, cancelled):
        if not self.entered.is_set():
            self.entered.set()
            assert self.release.wait(5)
        yield TextDelta("已处理")
        yield ModelTurn("已处理")


@pytest.fixture(scope="session")
def app():
    application = QApplication.instance() or QApplication([])
    yield application
    application.processEvents()


@pytest.fixture
def window(tmp_path, monkeypatch, app):
    monkeypatch.setattr(sessions_module, "create_backend", DeletableLoop)
    provider = ScriptedProvider([])
    context = BoundContext("instance", "generation", "source", {"valid": True})
    with SessionJournal(tmp_path, "history") as history:
        DeletableLoop(provider, ToolRegistry(), history, context)._event("session.created", {})
    with SessionJournal(tmp_path, "initial") as journal:
        loop = DeletableLoop(provider, ToolRegistry(), journal, context)
        loop._event("session.created", {})
        widget = AssistantWindow(loop)
        widget.navigation.timer.stop()
        _tick(widget)
        try:
            yield widget
        finally:
            widget.close()
            widget.sessions.wait(5)
            widget.deleteLater()
            app.sendPostedEvents(None, QEvent.DeferredDelete)
            app.processEvents()


def _trees(window):
    """左侧导航的两块区域：上"当前"（已打开会话）、下"历史"（未打开的记录）。"""

    return window.navigation.current, window.navigation.list


def _item(window, session_id):
    for tree in _trees(window):
        for index in range(tree.topLevelItemCount()):
            item = tree.topLevelItem(index)
            if item.data(0, Qt.UserRole) == session_id:
                return item
    raise LookupError("会话行不存在：" + session_id)


def _tree_of(window, session_id):
    for tree in _trees(window):
        for index in range(tree.topLevelItemCount()):
            if tree.topLevelItem(index).data(0, Qt.UserRole) == session_id:
                return tree
    return None


def _counts(window):
    """每块区域的行数：当前（已打开会话）与历史（未打开的记录）。"""

    current, history = _trees(window)
    return current.topLevelItemCount(), history.topLevelItemCount()


def _tick(window):
    settle(window, tasks=False)
    window.sessions.catalog.request().result(5)
    window.navigation.catalog_state = None
    window.navigation.timer.timeout.emit()


def _browse(window, session_id):
    """模拟双击历史行：鼠标走 ``itemDoubleClicked``（回车由行内快捷键触发）。"""

    item = _item(window, session_id)
    _tree_of(window, session_id).itemDoubleClicked.emit(item, 0)
    settle(window, tasks=False)


def _alert_texts(window):
    """Non-modal notices currently owned by the navigation pane."""

    texts = [value[1] for value in window.navigation.notices.messages.values()]
    if notice_text(window):
        texts.append(notice_text(window).partition("\n")[2])
    return texts


def _dot_color(item):
    """Center pixel of the painted status dot; never an image asset."""

    image = item.icon(0).pixmap(16, 16).toImage()
    color = image.pixelColor(8, 8)
    return color.red(), color.green(), color.blue()
