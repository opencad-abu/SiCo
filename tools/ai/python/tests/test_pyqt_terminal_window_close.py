"""Terminal close decisions, shutdown escalation and popup-safe cleanup."""

from __future__ import annotations

import pytest
from PyQt5.QtCore import QTimer
from PyQt5.QtGui import QCloseEvent

import terminal_fixtures  # noqa: F401  # installs the PyQt package path
from cad_ai_terminal import window_close as close_module
from cad_ai_terminal import window_shutdown as shutdown_module
from cad_ai_terminal.window_close import WindowCloseController
from cad_ai_terminal.window_shutdown import WindowShutdown


class Session:
    def __init__(self, title="会话", *, running=False, defer=False, finished=False):
        self.title = title
        self.running = running
        self.defer = defer
        self.finished = finished
        self.deferred = []
        self.stopped = 0
        self.begun = 0
        self.forced = 0

    def defer_context_operation(self, operation):
        self.deferred.append(operation)
        return self.defer

    def is_running(self):
        return self.running

    def stop_monitor(self):
        self.stopped += 1

    def begin_shutdown(self):
        self.begun += 1

    def force_shutdown(self):
        self.forced += 1


class Dialog:
    queue = []
    instances = []

    def __init__(self, _title, _message, _parent):
        self.choices = []
        self.object_name = ""
        self.rejected = 0
        self.choice = None
        type(self).instances.append(self)

    def setObjectName(self, value):
        self.object_name = value

    def add_choice(self, key, _label, **_kwargs):
        self.choices.append(key)

    def ask(self):
        self.choice = type(self).queue.pop(0)
        return self.choice

    def reject(self):
        self.rejected += 1


class Shutdown:
    def __init__(self, _parent, sessions, release, finish):
        self._sessions = sessions
        self._release = release
        self._finish = finish
        self.active = False
        self.begin_calls = 0
        self.stop_calls = 0

    def begin(self):
        self.begin_calls += 1
        self.active = True
        for session in self._sessions():
            session.begin_shutdown()

    def stop(self):
        self.stop_calls += 1


@pytest.fixture
def controller(monkeypatch):
    sessions = []
    events = []
    Dialog.queue[:] = []
    Dialog.instances[:] = []
    monkeypatch.setattr(close_module, "SiConfirm", Dialog)
    monkeypatch.setattr(close_module, "WindowShutdown", Shutdown)
    monkeypatch.setattr(close_module.QTimer, "singleShot",
                        staticmethod(lambda _delay, callback: callback()))
    value = WindowCloseController(
        None,
        sessions=lambda: tuple(sessions),
        remove_session=lambda session: (events.append(("remove", session)),
                                        sessions.remove(session)),
        release_sessions=lambda: events.append("release"),
        close_window=lambda: events.append("close"),
        minimize_window=lambda: events.append("minimize"),
        sync_current=lambda: events.append("sync"),
        cancel_rename=lambda **_kwargs: events.append("cancel-rename"),
    )
    return value, sessions, events


def test_close_empty_sets_promptless_mode_and_requests_window_close(controller):
    value, _sessions, events = controller
    value.close_empty()
    assert value.closing is True
    assert events == ["close"]


def test_request_close_handles_unknown_deferred_running_and_promptless(controller):
    value, sessions, events = controller
    unknown = Session()
    value.request_close_session(unknown, confirm=True)
    assert events == []

    deferred = Session(defer=True)
    sessions.append(deferred)
    value.request_close_session(deferred, confirm=True)
    assert deferred.deferred and events == []

    stopped = Session(running=False)
    sessions.append(stopped)
    value.request_close_session(stopped, confirm=False)
    assert events[-1] == ("remove", stopped)

    running = Session(running=True)
    sessions.append(running)
    Dialog.queue[:] = ["cancel"]
    value.request_close_session(running, confirm=True)
    assert events == [("remove", stopped)]

    Dialog.queue[:] = ["exit"]
    value.request_close_session(running, confirm=True)
    assert events[-1] == ("remove", running)

    value._close_without_prompt = True
    another = Session(running=True)
    sessions.append(another)
    value.request_close_session(another, confirm=True)
    assert events[-1] == ("remove", another)


def test_session_finished_keeps_last_tab_and_prunes_or_rejects_dialogs(controller):
    value, sessions, events = controller
    last = Session(running=False)
    sessions.append(last)
    value.session_finished(last)
    assert last.stopped == 1 and events[-1] == "sync"

    first = Session(running=False)
    second = Session(running=False)
    sessions[:] = [first, second]
    value.session_finished(first)
    assert events[-1] == ("remove", first)

    target = Session(running=True)
    sessions[:] = [target]
    dialog = Dialog("", "", None)
    value._tab_close_dialog = (dialog, target)
    value.session_finished(target)
    assert dialog.rejected == 1

    close_dialog = Dialog("", "", None)
    sessions[:] = [Session(running=False)]
    value._close_dialog = close_dialog
    value.session_finished(sessions[0])
    assert close_dialog.rejected == 1
    assert value.closing is True


def test_handle_close_accepts_idle_and_escalates_running_sessions(controller):
    value, sessions, events = controller
    event = QCloseEvent()
    value.handle_close(event)
    assert event.isAccepted() and events[-1] == "release"

    sessions[:] = [Session(running=True)]
    event = QCloseEvent()
    Dialog.queue[:] = ["minimize"]
    value.handle_close(event)
    assert not event.isAccepted()
    assert events[-1] == "minimize"

    Dialog.queue[:] = ["exit"]
    value.handle_close(QCloseEvent())
    assert value._shutdown.begin_calls == 1

    value._shutdown.active = True
    ignored = QCloseEvent()
    value.handle_close(ignored)
    assert not ignored.isAccepted()


def test_handle_close_defers_active_context_menu_and_owner_shutdown(controller):
    value, sessions, events = controller
    session = Session(running=True, defer=True)
    sessions[:] = [session]
    event = QCloseEvent()
    value.handle_close(event)
    assert not event.isAccepted()
    assert session.deferred

    value.shutdown_from_owner()
    assert value.closing is True and "close" in events
    assert "cancel-rename" in events

    value._close_dialog = Dialog("", "", None)
    value.shutdown_from_owner()
    assert value._close_dialog.rejected == 1

    value._close_dialog = None
    value._tab_close_dialog = (Dialog("", "", None), session)
    value.shutdown_from_owner()
    assert value._tab_close_dialog[0].rejected == 1


def test_window_shutdown_escalates_hup_to_kill_and_releases(monkeypatch):
    calls = []
    sessions = [Session(running=True)]

    class FakeTimer:
        def __init__(self, _parent):
            self.timeout = type("Signal", (), {"connect": lambda _self, _fn: None})()

        def start(self, milliseconds):
            calls.append(("start", milliseconds))

        def setSingleShot(self, _enabled):
            pass

        def stop(self):
            calls.append("stop")

    monkeypatch.setattr(shutdown_module, "QTimer", FakeTimer)
    value = WindowShutdown(
        None,
        lambda: tuple(sessions),
        lambda: calls.append("release"),
        lambda: calls.append("close"),
    )
    value.begin()
    assert value.active and sessions[0].begun == 1
    assert ("start", value.GRACE_MS) in calls
    value._force()
    assert sessions[0].forced == 1 and ("start", value.SETTLE_MS) in calls
    value._finish()
    assert calls[-2:] == ["release", "close"]
    assert value.active is False
    value.stop()
    assert "stop" in calls
