"""Real Qt rename and timer paths keep session names separate from provider labels."""

from __future__ import annotations

import os
import queue
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import agent_sessions_harness as sessions_module
from agent_window_fixture import create_window as AssistantWindow
from PyQt5.QtCore import QEvent, Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

import sico_ui.navigation as navigation_module
from sico.codex.backend import CodexBackend
from sico.codex.rpc import AppServer
from sico.core.contracts import BoundContext
from sico.core.tools import ToolRegistry
from sico.storage.journal import SessionJournal
from info_app_fixtures import dismiss_notice, notice_text

LABEL = "Codex 0.162.1 / test-model / native"


class MetadataLoop(CodexBackend):
    def __init__(self, provider, tools, journal, context, **_options):
        super().__init__(provider, tools, journal, context)

    def _ensure_runtime(self):
        if not self.thread_id:
            self._bind_thread("start", {"id": self.journal.session_id}, expected=None)

    def set_thread_name(self, name):
        self.names._name(self.names.validate(name), "user")


@pytest.fixture(scope="session")
def app():
    application = QApplication.instance() or QApplication([])
    yield application
    application.processEvents()


@pytest.fixture
def window(tmp_path, monkeypatch, app):
    monkeypatch.setattr(sessions_module, "create_backend", MetadataLoop)
    provider = SimpleNamespace(label=LABEL)
    context = BoundContext("instance", "generation", "source", {"valid": True})
    with SessionJournal(tmp_path, "history") as journal:
        MetadataLoop(provider, ToolRegistry(), journal, context)._ensure_runtime()
    with SessionJournal(tmp_path, "initial") as journal:
        loop = MetadataLoop(provider, ToolRegistry(), journal, context)
        loop._ensure_runtime()
        widget = AssistantWindow(loop)
        widget.navigation.timer.stop()
        try:
            wait_for_names(widget, {"initial": "当前会话\ninitial"})
            yield widget
        finally:
            widget.close()
            widget.sessions.wait(3)
            widget.deleteLater()
            app.sendPostedEvents(None, QEvent.DeferredDelete)
            app.processEvents()


def item(window, session_id):
    """会话行可能在上方"当前"（已打开）或下方"历史"（未打开）。"""

    for tree in (window.navigation.current, window.navigation.list):
        for index in range(tree.topLevelItemCount()):
            if tree.topLevelItem(index).data(0, Qt.UserRole) == session_id:
                return tree.topLevelItem(index)
    raise LookupError("会话行不存在：" + session_id)


def settle(window, *, tasks=True):
    deadline = time.monotonic() + 5
    while True:
        QTest.qWait(10)
        commands = getattr(window, "commands", None)
        if commands:
            commands.poll()
        window.binding.poll()
        if (not window.sessions.busy and not getattr(commands, "pending", False)
                and not window.page.opening):
            return
        assert time.monotonic() < deadline, window.status.text()


def activate(window, session_id):
    window.page.activate_session(session_id)
    settle(window)


def wait_for_names(window, expected):
    catalog = getattr(window.sessions, "catalog", None)
    deadline = time.monotonic() + 8
    requested = 0
    while time.monotonic() < deadline:
        if catalog and time.monotonic() - requested >= 0.1:
            catalog.request()
            requested = time.monotonic()
        window.navigation.refresh()
        try:
            if all(item(window, key).text(0) == value for key, value in expected.items()):
                return
        except StopIteration:
            pass
        QTest.qWait(10)
    assert {key: item(window, key).text(0) for key in expected} == expected


@pytest.mark.parametrize("existing", [None, "Native title", "Manual name"])
def test_rename_defaults_to_session_metadata_and_preserves_model_on_switch(
    window, monkeypatch, existing,
):
    loop = window.controller.loop
    if existing:
        key = "preview" if existing == "Native title" else "name"
        loop.names.capture({"id": "initial", key: existing})
        wait_for_names(window, {"initial": existing})
    seen = []
    title = window.windowTitle()

    def rename(*args, **kwargs):
        seen.append(kwargs["value"])
        return "Review A12", True

    monkeypatch.setattr("sico_ui.navigation_actions.ask_text", rename)
    window.navigation._rename("initial")
    settle(window)
    assert seen == [existing or "initial"]
    assert window.controller.display_name == "Review A12"
    wait_for_names(window, {"initial": "Review A12"})
    activate(window, "history")
    activate(window, "initial")
    window.navigation._rename("initial")
    settle(window)
    assert seen[-1] == "Review A12"
    assert window.controller.label == LABEL
    assert window.model_status.text() == LABEL
    assert window.windowTitle() == title


def test_rename_default_prefers_live_controller_metadata(window, monkeypatch):
    window.controller.loop.names._name("Live notification", "codex.name")
    window.navigation.catalog_snapshot = {"rows": (), "retained": {}}
    seen = []

    def rename(*args, **kwargs):
        seen.append(kwargs["value"])
        return "Updated", True

    monkeypatch.setattr("sico_ui.navigation_actions.ask_text", rename)
    window.navigation._rename("initial")
    settle(window)
    assert seen == ["Live notification"]


def test_rename_default_does_not_wait_for_background_controller_command(window, monkeypatch):
    controller = window.controller
    controller.loop.names._name("Saved display name", "user")
    entered, release = threading.Event(), threading.Event()

    def slow_rename(name):
        entered.set()
        assert release.wait(5)

    monkeypatch.setattr(controller.loop, "set_thread_name", slow_rename)
    with ThreadPoolExecutor(max_workers=2) as reader:
        operation = reader.submit(controller.rename, "New name")
        try:
            assert entered.wait(5)
            result = reader.submit(lambda: controller.display_name)
            assert result.result(1) == "Saved display name"
        finally:
            release.set()
        operation.result(5)
    settle(window)


def test_rename_cancel_and_session_switch_in_dialog_do_not_change_names(window, monkeypatch):
    monkeypatch.setattr("sico_ui.navigation_actions.ask_text",
                        lambda *args, **kwargs: ("Ignored", False))
    window.navigation._rename("initial")
    settle(window)
    assert window.controller.display_name == "initial"
    assert not notice_text(window)

    def switched(*args, **kwargs):
        window.page.activate_session("history")
        return "Wrong session", True

    monkeypatch.setattr("sico_ui.navigation_actions.ask_text", switched)
    window.navigation._rename("initial")
    settle(window)
    assert "所选会话已切换" in notice_text(window)
    dismiss_notice(window)
    assert window.sessions.controllers["initial"].display_name == "initial"
    assert window.controller.display_name == "history"
    assert window.model_status.text() == LABEL


def test_timer_updates_unselected_idle_name_and_retained_row_without_provider_label(
    window, monkeypatch, app, tmp_path,
):
    window.page.activate_session("history")
    settle(window, tasks=False)
    loop = window.sessions.controllers["initial"].loop
    rpc = object.__new__(AppServer)
    rpc.pending, rpc.messages = deque(), queue.Queue()
    rpc.notification_handler = loop.names.notification
    rpc.messages.put({"method": "thread/name/updated", "params": {
        "threadId": "initial", "threadName": "LDO A12 stability review",
    }})
    loop.runtime = SimpleNamespace(rpc=rpc, close=lambda: None)
    catalog = getattr(window.sessions, "catalog", None)
    if catalog:
        snapshot = catalog.snapshot

        def bounded_snapshot(since_version=None):
            value = snapshot(since_version)
            if value:
                # Never mutate the service's published snapshot: filtering must
                # return a copy, or the version/retained bookkeeping breaks.
                value = {
                    **value,
                    "rows": tuple(row for row in value["rows"] if row["id"] != "initial"),
                }
            return value

        monkeypatch.setattr(catalog, "snapshot", bounded_snapshot)
    else:
        read_catalog = navigation_module.session_catalog
        monkeypatch.setattr(navigation_module, "session_catalog", lambda root: [
            row for row in read_catalog(root) if row["id"] != "initial"
        ])
    window.navigation.timer.start(1)
    try:
        wait_for_names(window, {"initial": "LDO A12 stability review"})
        assert window.controller.session_id == "history"
        assert window.model_status.text() == LABEL
        assert LABEL not in item(window, "history").text(0)
        window.show()
        app.processEvents()
        assert window.grab().save(str(tmp_path / "session-names.png"))
    finally:
        window.navigation.timer.stop()
        loop.runtime = None
