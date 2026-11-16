"""The recovery panel owns only one dialog and one explicit durable intent."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PyQt5.QtCore import QObject
from PyQt5.QtWidgets import QLineEdit, QVBoxLayout
from sico_ui import session_recovery
from sico_ui.session_recovery import SessionRecovery


class Action:
    def __init__(self):
        self.visible = True
        self.enabled = True

    def setVisible(self, value):
        self.visible = value

    def setEnabled(self, value):
        self.enabled = value


class Receipt:
    def __init__(self):
        self.pending = None
        self.cleared = 0

    def clear(self):
        self.cleared += 1
        self.pending = None

    def watch(self, future, success, failure):
        self.pending = (future, success, failure)


class Status:
    def __init__(self):
        self.messages = []

    def showMessage(self, message):
        self.messages.append(message)

    def clearMessage(self):
        self.messages.append("")


class Page:
    def __init__(self):
        self.activation = 1
        self.reviewing = None
        self.opening = False
        self.session = SimpleNamespace(session_id="live")
        self.continued = []
        self.activated = []

    def continue_session(self, key):
        self.continued.append(key)

    def activate_recovered(self, key):
        self.activated.append(key)


class Api:
    def __init__(self):
        self.recovery = SimpleNamespace(inspect=None, restore=None)
        self.commands = []

    def session(self, key):
        return "token:" + key

    def can_control(self, _token):
        return True

    def command(self, token, operation, *args, **kwargs):
        self.commands.append((token, operation, args, kwargs))
        return "command-future"


class Window(QObject):
    def __init__(self):
        super().__init__()
        self.lifecycle = SimpleNamespace(closing=False)
        self.page = Page()
        self.api = Api()
        self.warnings = []
        self.info_app = SimpleNamespace(
            warning=lambda *args: self.warnings.append(args))
        self.status = Status()
        self.action = Action()

    def statusBar(self):
        return self.status

    def session_action(self, _text, callback):
        self.action.callback = callback
        return self.action


class Dialog:
    instances = []

    def __init__(self, *args):
        self.args = args
        self.layout = QVBoxLayout()
        self.shown = False
        self.closed = 0
        self.deleted = 0
        Dialog.instances.append(self)

    def setModal(self, value):
        self.modal = value

    def content_layout(self):
        return self.layout

    def show(self):
        self.shown = True

    def close(self):
        self.closed += 1

    def deleteLater(self):
        self.deleted += 1

    def reject(self):
        self.closed += 1


class Signal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def emit(self, value):
        for callback in self.callbacks:
            callback(value)


class Confirm:
    instances = []

    def __init__(self, *args):
        self.args = args
        self.chosen = Signal()
        self.choices = []
        self.shown = False
        Confirm.instances.append(self)

    def add_choice(self, *args, **kwargs):
        self.choices.append((args, kwargs))

    def open(self):
        self.shown = True

    def close(self):
        self.shown = False

    def deleteLater(self):
        pass


@pytest.fixture
def panel(monkeypatch):
    window = Window()
    value = SessionRecovery(window)
    value.timer.stop()
    value.receipt = Receipt()
    Dialog.instances.clear()
    Confirm.instances.clear()
    monkeypatch.setattr(session_recovery, "SiDialog", Dialog)
    monkeypatch.setattr(session_recovery, "SiConfirm", Confirm)
    yield value
    value.timer.stop()
    value.dismiss()


def view(state="ready", **overrides):
    value = dict(state=state, credential=None, inputs=[], operation=None,
                 session_id="live", task_id="task", task_status="")
    value.update(overrides)
    return value


def test_refresh_handles_closing_stale_browsing_and_action_state(panel):
    window = panel.window
    panel.scope = (99, "old")
    panel.refresh()
    assert panel.receipt.cleared == 1
    assert panel.action.enabled is True

    panel.browsing = "old"
    window.page.reviewing = None
    panel.refresh()
    assert panel.browsing is None

    panel.scope = None
    window.page.reviewing = "old"
    panel.browsing = "old"
    inspected = []
    panel.inspect = lambda: inspected.append(True)
    panel.refresh()
    assert panel.browsing is None and inspected == [True]

    window.page.reviewing = "other"
    panel.browsing = "old"
    panel.refresh()
    assert panel.browsing == "old"

    window.lifecycle.closing = True
    panel.refresh()
    assert panel.action.enabled is False


def test_failed_and_recover_only_affect_the_current_scope(panel):
    window = panel.window
    panel.scope = panel._scope()
    panel.failed(RuntimeError("lost"))
    assert window.status.messages[-1] == ""
    assert "lost" in window.warnings[-1][1]

    panel.scope = (99, "old")
    panel.failed(RuntimeError("ignored"))
    assert len(window.warnings) == 1
    window.lifecycle.closing = True
    panel.recover("old")
    assert window.page.continued == []
    window.lifecycle.closing = False
    panel.recover("old")
    assert panel.receipt.cleared == 1
    assert window.page.continued == ["old"]


def test_inspect_is_guarded_and_tracks_the_durable_operation(panel):
    window = panel.window
    panel.operations["live"] = "old-operation"
    calls = []
    window.api.recovery.inspect = lambda key, operation: calls.append((key, operation)) or "future"

    window.page.opening = True
    panel.inspect()
    assert calls == []
    window.page.opening = False
    window.lifecycle.closing = True
    panel.inspect()
    assert calls == []
    window.lifecycle.closing = False
    panel.inspect()
    assert calls == [("live", "old-operation")]
    assert panel.scope == panel._scope()
    assert panel.receipt.pending[0] == "future"
    assert "正在读取" in window.status.messages[-1]


@pytest.mark.parametrize("state", ["ready", "waiting_credentials", "live",
                                   "ended", "archived_readonly", "legacy_readonly"])
def test_present_builds_each_state_without_another_writer(monkeypatch, state):
    window = Window()
    value = SessionRecovery(window)
    value.timer.stop()
    value.receipt = Receipt()
    monkeypatch.setattr(session_recovery, "SiDialog", Dialog)
    monkeypatch.setattr(session_recovery, "SiConfirm", Confirm)
    try:
        value.scope = value._scope()
        window.api.recovery.inspect = lambda *_args: "future"
        window.api.session = lambda _key: "token"
        if state == "live":
            window.api.can_control = lambda _token: True
        value.present(view(state, credential="MODEL_KEY" if state == "waiting_credentials"
                          else None, inputs=[{"input_id": "one", "status": "queued"}],
                          operation={"operation_id": "op"}))
        assert value.dialog is not None and value.dialog.shown
        assert value.dialog.layout.count() == 1
        assert value.dialog.layout.itemAt(0).layout().count() >= 2
    finally:
        value.timer.stop()
        value.dismiss()


def test_present_ignores_a_dialog_when_the_scope_is_stale(panel):
    panel.window.page.activation += 1
    panel.present(view())
    assert panel.dialog is None


def test_restore_clears_the_secret_and_records_one_operation(panel):
    window = panel.window
    panel.scope = panel._scope()
    field = QLineEdit()
    field.setText("secret")
    calls = []
    window.api.recovery.restore = (
        lambda durable, operation, credentials:
        calls.append((durable, operation, credentials)) or "restore-future")
    durable = view(credential="MODEL_KEY")
    panel.restore(durable, field)
    assert field.text() == ""
    assert calls[0][0] is durable
    assert calls[0][2] == {"MODEL_KEY": "secret"}
    assert panel.operations["live"] == calls[0][1]
    assert panel.receipt.pending[0] == "restore-future"

    panel.scope = (99, "old")
    panel.restore(durable, field)
    assert len(calls) == 1


def test_attach_and_command_reject_stale_or_unowned_sessions(panel):
    window = panel.window
    panel.scope = panel._scope()
    panel.attach("live")
    assert window.page.activated == ["live"]
    panel.scope = (99, "old")
    panel.attach("ignored")
    assert window.page.activated == ["live"]

    panel.scope = panel._scope()
    window.page.reviewing = "old"
    panel._command(view(), "recover_input", "input")
    assert window.api.commands == []
    window.page.reviewing = None
    window.api.can_control = lambda _token: False
    panel._command(view(), "recover_input", "input")
    assert window.api.commands == []
    window.api.can_control = lambda _token: True
    panel._command(view(), "recover_input", "input")
    assert window.api.commands == [("token:live", "recover_input", ("input",), {})]
    assert panel.receipt.pending[0] == "command-future"


def test_confirm_selection_and_task_are_explicit(panel):
    window = panel.window
    panel.scope = panel._scope()
    panel.select(view(inputs=[{"input_id": "one", "status": "queued"}]), 0,
                 "recover_input")
    assert window.api.commands[-1][1] == "recover_input"
    panel.select(view(inputs=[{"input_id": "one", "status": "queued"}]), 0,
                 "abandon_input")
    dialog = Confirm.instances[-1]
    dialog.chosen.emit("cancel")
    assert len(window.api.commands) == 1
    dialog.chosen.emit("accept")
    assert window.api.commands[-1][1] == "abandon_input"

    panel.task(view(task_id="task"), "resume")
    assert window.api.commands[-1][3] == {"expected_task": "task"}
    panel.task(view(task_id="task"), "acknowledge_interrupted")
    dialog = Confirm.instances[-1]
    dialog.chosen.emit("accept")
    assert window.api.commands[-1][1] == "acknowledge_interrupted"
    assert window.api.commands[-1][3] == {"expected_task": "task"}

    panel.select(view(inputs=[]), 0, "recover_input")
    panel.task(view(), "resume")
