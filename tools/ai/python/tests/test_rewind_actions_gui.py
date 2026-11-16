"""Per-message rewind asks for a fresh scope and never overwrites a draft."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace

import pytest
from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtWidgets import QApplication, QPlainTextEdit, QWidget
from sico.service.chat_projection import ChatProjection
from sico_ui import rewind_actions as module
from sico_ui.rewind_actions import RewindActions


class Page(QObject):
    invalidated = pyqtSignal()


class Api:
    def __init__(self, handle):
        self.handle = handle

    def owns(self, handle):
        return handle is self.handle

    def connection(self, _handle):
        return ("instance", "generation")

    def is_closing(self, _handle):
        return False


class Notices:
    def __init__(self):
        self.rows = []

    def warning(self, title, message):
        self.rows.append(("warning", title, message))

    def notice(self, title, message):
        self.rows.append(("notice", title, message))

    def error(self, title, message):
        self.rows.append(("error", title, message))


class StatusBar:
    def __init__(self):
        self.messages = []

    def showMessage(self, message, *args):
        self.messages.append(message)


@pytest.fixture
def surface(monkeypatch):
    app = QApplication.instance() or QApplication([])
    parent = QWidget()
    page = Page()
    page.session, page.activation, page.reviewing, page.opening = object(), 1, None, False
    context = SimpleNamespace(instance_id="instance", generation="generation")
    projection = ChatProjection()
    projection.messages = [
        {
            "role": "user",
            "text": "原来的问题",
            "message_id": 11,
            "turn_id": "turn-1",
            "thread_id": "thread",
            "rewindable": True,
            "revision": 0,
            "source_sequence": 1,
            "timestamp": "",
        }
    ]
    parent.chat_scroll = SimpleNamespace(shown=lambda: projection)
    notices, status_bar = Notices(), StatusBar()
    widgets = SimpleNamespace(
        parent=parent, notices=notices, status_bar=status_bar, input=QPlainTextEdit()
    )
    presentation = SimpleNamespace(
        displayed_context=context, state={"busy": False, "pending": 0, "task": {}}
    )
    lifecycle = SimpleNamespace(closing=False)
    calls = []

    def run_command(name, *args, **kwargs):
        calls.append((name, args, kwargs))
        if name == "thread_status":
            kwargs["success"](
                {
                    "history_mode": "paginated",
                    "thread_id": "thread",
                    "busy": False,
                    "operation": None,
                    "scope": {"thread_id": "thread"},
                }
            )
        elif name == "thread_operation":
            kwargs["success"](kwargs["request_id"])
        return None

    actions = RewindActions(
        widgets,
        api=Api(page.session),
        page=page,
        presentation=presentation,
        lifecycle=lifecycle,
        run_command=run_command,
    )
    monkeypatch.setattr(module, "confirm", lambda *_args, **_kwargs: True)
    try:
        yield actions, widgets, calls
    finally:
        actions.deleteLater()
        parent.close()
        parent.deleteLater()
        app.processEvents()


def test_rewind_dispatches_status_then_operation_and_restores_prompt(surface):
    actions, widgets, calls = surface
    actions.open("11")
    assert [call[0] for call in calls] == ["thread_status", "thread_operation"]
    operation = calls[1]
    assert operation[1] == ({"kind": "rollback_to", "before_turn_id": "turn-1"},)
    assert operation[2]["scope"] == {"thread_id": "thread"}
    assert operation[2]["request_id"]
    actions.reverted({"payload": {"thread_id": "thread", "before_turn_id": "turn-1"}})
    assert widgets.input.toPlainText() == "原来的问题"
    assert widgets.status_bar.messages[-1] == "回退请求已接收"


def test_rewind_keeps_an_existing_draft(surface):
    actions, widgets, _calls = surface
    widgets.input.setPlainText("未发送的草稿")
    actions.open("11")
    actions.reverted({"payload": {"thread_id": "thread", "before_turn_id": "turn-1"}})
    assert widgets.input.toPlainText() == "未发送的草稿"
    assert widgets.notices.rows[-1][0] == "notice"


def test_rewind_rejects_a_busy_session_before_status(surface):
    actions, widgets, calls = surface
    actions.presentation.state = {"busy": True, "pending": 0, "task": {}}
    actions.open("11")
    assert calls == []
    assert widgets.notices.rows[-1][:2] == ("warning", "暂时不能回退")


def test_rewind_rejects_a_message_from_another_thread(surface):
    actions, widgets, calls = surface

    def run_command(name, *args, **kwargs):
        calls.append((name, args, kwargs))
        if name == "thread_status":
            kwargs["success"]({
                "history_mode": "paginated", "thread_id": "other", "busy": False,
                "operation": None, "scope": {"thread_id": "other"},
            })
        return None

    actions.run_command = run_command
    actions.open("11")
    assert [call[0] for call in calls] == ["thread_status"]
    assert widgets.notices.rows[-1][:2] == ("warning", "无法回退")


def test_rewind_rejects_legacy_history_before_operation(surface):
    actions, widgets, calls = surface

    def run_command(name, *args, **kwargs):
        calls.append((name, args, kwargs))
        if name == "thread_status":
            kwargs["success"](
                {"history_mode": "legacy", "thread_id": "thread", "scope": {"thread_id": "thread"}}
            )
        return None

    actions.run_command = run_command
    actions.open("11")
    assert [call[0] for call in calls] == ["thread_status"]
    assert widgets.notices.rows[-1][:2] == ("warning", "无法回退")
