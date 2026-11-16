"""Busy desktop inputs queue without changing the running target or losing the draft."""

from __future__ import annotations

import json
import os
import threading
import time
from agent_gui_wait import settle

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

from sico.adapters.virtuoso import context_tools
from sico.core.contracts import BoundContext
from sico.service.python_backend import PythonBackend
from sico.providers.mock import ContextDemoProvider
from sico.storage.journal import SessionJournal
from agent_quick_fixture import QuickInput
from agent_window_fixture import create_window as AssistantWindow


def pump(app, condition, timeout=5):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert condition()


def test_busy_queue_has_independent_targets_and_keeps_manual_draft(tmp_path):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    initial = BoundContext("i", "g", "bound", {"valid": True})
    entered, release = threading.Event(), threading.Event()
    observed, notices = [], []

    class Broker:
        def register_target(self, context):
            pass

        def release_target(self, context):
            pass

        def get_context(self, context):
            observed.append(context.target_id)
            if context.target_id == "q1":
                entered.set()
                assert release.wait(5)
            return context.snapshot

    with SessionJournal(tmp_path, "queue") as journal:
        broker = Broker()
        loop = PythonBackend(ContextDemoProvider(), context_tools(broker), journal, initial)
        window = AssistantWindow(loop)
        window.quick_input = QuickInput(
            window, broker, lambda kind, **data: notices.append((kind, data))
        )
        window.input.setPlainText("Unsent manual draft")

        def submit(key):
            context = BoundContext("i", "g", key, {"valid": True, "window": key})
            window.quick_input.accept(
                {
                    "kind": "submit",
                    "id": key,
                    "text": "Read source " + key,
                    "context": context.record(),
                }
            )

        try:
            submit("q1")
            pump(app, entered.is_set)
            submit("q2")
            submit("q3")
            settle(window, tasks=False)
            assert observed == ["q1"]
            assert loop.state.context.target_id == "q1"
            assert len(window.controller.inbox.pending) == 2
            assert window.input.toPlainText() == "Unsent manual draft"
            release.set()
            pump(app, lambda: observed == ["q1", "q2", "q3"] and not window.controller.busy)
            window.binding.poll()
            assert loop.state.context.target_id == "q3"
            assert window.input.toPlainText() == "Unsent manual draft"
            records = [
                json.loads(path.read_text())
                for path in (journal.directory / "inbox").glob("*.json")
            ]
            assert len(records) == 3 and all(record["status"] == "completed" for record in records)
            assert [data["id"] for kind, data in notices if kind == "accepted"] == [
                "q1",
                "q2",
                "q3",
            ]
            previous = window.controller
            window.quick_input.new.trigger()
            settle(window)
            assert window.quick_input.new.text() == "新会话"
            assert window.controller is not previous
            assert window.controller.session_id in window.sessions.controllers
            assert len(list((journal.root / "sessions").iterdir())) == 2
        finally:
            release.set()
            pump(app, lambda: not window.controller.busy)
            window.quick_input.close()
            window.close()


def test_invalid_quick_input_keeps_desktop_and_manual_draft(tmp_path):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    initial = BoundContext("i", "g", "bound", {"valid": True})
    notices = []

    class Broker:
        def register_target(self, context):
            raise AssertionError("Invalid input reached target registration")

    with SessionJournal(tmp_path, "invalid") as journal:
        broker = Broker()
        loop = PythonBackend(ContextDemoProvider(), context_tools(broker), journal, initial)
        window = AssistantWindow(loop)
        window.quick_input = QuickInput(
            window, broker, lambda kind, **data: notices.append((kind, data))
        )
        window.input.setPlainText("Keep this draft")
        try:
            for invalid in (None, [], {}, {"kind": "submit", "id": "bad id"}):
                window.quick_input.accept(invalid)
            assert notices == []
            window.quick_input.accept(
                {"kind": "submit", "id": "q1", "text": "Read source", "context": None}
            )
            settle(window)
            assert notices == [("rejected", {"id": "q1"})]
            assert window.input.toPlainText() == "Keep this draft"
            assert not window.controller.busy and not window.controller.inbox.pending
            assert not list((journal.directory / "inbox").iterdir())
        finally:
            window.quick_input.close()
            window.close()
