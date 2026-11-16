"""Bootstrap I/O, credentials and cancellation remain responsive in Qt."""

import os
import queue
import threading
from concurrent.futures import CancelledError
from types import SimpleNamespace

import pytest
from agent_gui_wait import settle, wait_until
from agent_startup_fixture import startup_backend

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import agent_startup_harness as startup_module
from agent_startup_harness import DesktopStartup
from PyQt5.QtCore import QTimer
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QLineEdit

from sico.core.contracts import BoundContext, ModelTurn, RunState, TextDelta, ToolCall
from sico.providers.mock import ScriptedProvider
from sico.service.startup_result import StartupFailure
from sico.storage.journal import SessionJournal
from sico.storage import journal_recovery
from sico_ui.si_prompt import SiPrompt
from sico_ui.startup import StartupView


@pytest.fixture
def setup(tmp_path, monkeypatch):
    owner = threading.get_ident()
    context = BoundContext("instance", "generation", "target", {"valid": True})
    args = SimpleNamespace(launch_dir=tmp_path, session="initial", provider_config=None)
    with SessionJournal(tmp_path, args.session) as journal:
        journal.append("session.created", {}, RunState(context))
    descriptor = {
        "instance_id": "instance",
        "generation": "generation",
        "bridge_id": "bridge",
        "router_id": "router",
    }
    calls, notices = [], []

    class Broker:
        def __init__(self, descriptor, *, cancelled=None):
            assert threading.get_ident() != owner
            self.descriptor, self.bridge_id = descriptor, "bridge"
            self.cancelled = cancelled
            self.closed = False
            calls.append("bridge")

        def context(self, *args, **kwargs):
            assert threading.get_ident() != owner
            assert not self.closed
            calls.append("context")
            return context

        def get_context(self, incoming):
            assert threading.get_ident() != owner
            assert not self.closed
            assert incoming == context
            calls.append("get_context")
            return context.snapshot

        def register_session(self, *args):
            assert threading.get_ident() != owner
            calls.append("register")

        def release_session(self, *args):
            assert threading.get_ident() != owner
            calls.append("release")

        def close(self):
            assert threading.get_ident() != owner
            self.closed = True
            calls.append("closed")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    monkeypatch.setattr(startup_module, "BridgeClient", Broker)

    class Notices:
        failed = threading.Event()

        def __call__(self, kind, **payload):
            notices.append((kind, payload))

    def create(**options):
        startup = DesktopStartup(args, Notices(), environment={}, **options)
        reader = SimpleNamespace(
            closed=threading.Event(), lines=queue.Queue(), close=lambda **kw: None
        )
        view = StartupView(startup, reader, Notices(), quit_application=lambda: None)
        reader.lines.put({"context": context.record(), "bridge": descriptor})
        instances.append((startup, view))
        return startup, view, reader

    instances = []
    yield SimpleNamespace(
        create=create,
        args=args,
        calls=calls,
        notices=notices,
        broker=Broker,
        context=context,
        owner=owner,
    )
    for startup, view in instances:
        view.close()
        assert startup.wait(5)
        if view.window is not None:
            view.window.lifecycle.persistent = False
            view.window.close()
            view.window.deleteLater()
        view.progress.deleteLater()
        view.deleteLater()
    QApplication.instance().processEvents()


def heartbeat():
    timer, ticks = QTimer(), []
    timer.timeout.connect(lambda: ticks.append(True))
    timer.start(5)
    try:
        QTest.qWait(60)
        assert len(ticks) >= 2
    finally:
        timer.stop()


@pytest.mark.parametrize("stage", ["bridge", "context", "history", "config", "provider", "session"])
def test_slow_startup_stages_run_off_qt_and_keep_event_loop_alive(setup, monkeypatch, stage):
    entered, release = threading.Event(), threading.Event()
    target, attribute = {
        "bridge": (setup.broker, "__init__"),
        "context": (setup.broker, "context"),
        "history": (journal_recovery, "writable_index"),
        "config": (startup_module, "resolve_provider_config"),
        "provider": (startup_module, "provider_from_config"),
        "session": (startup_module, "SessionController"),
    }[stage]
    original = getattr(target, attribute)

    def slow(*args, **kwargs):
        assert threading.get_ident() != setup.owner
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(target, attribute, slow)
    startup, view, _ = setup.create()
    try:
        wait_until(entered.is_set)
        assert view.window is None and view.progress.isVisible()
        heartbeat()
        release.set()
        wait_until(lambda: view.window is not None)
        settle(view.window)
        assert startup.ready.done()
        assert setup.calls[:2] == ["bridge", "context"]
        assert "register" in setup.calls
        assert [kind for kind, _ in setup.notices][:2] == ["ready", "shown"]
        assert not view.progress.isVisible()
    finally:
        release.set()


@pytest.mark.parametrize("shutdown", ["command", "eof", "cancel"])
@pytest.mark.parametrize("stage", ["bridge", "context", "history", "config", "session"])
def test_shutdown_during_startup_discards_late_results_and_closes_resources(
    setup,
    monkeypatch,
    stage,
    shutdown,
):
    entered, release = threading.Event(), threading.Event()
    target, attribute = {
        "bridge": (setup.broker, "__init__"),
        "context": (setup.broker, "context"),
        "history": (journal_recovery, "writable_index"),
        "config": (startup_module, "resolve_provider_config"),
        "session": (startup_module, "SessionController"),
    }[stage]
    original = getattr(target, attribute)

    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(target, attribute, slow)
    startup, view, reader = setup.create()
    try:
        wait_until(entered.is_set)
        if shutdown == "command":
            reader.lines.put({"kind": "shutdown"})
        elif shutdown == "eof":
            reader.closed.set()
        else:
            view.progress.canceled.emit()
        wait_until(lambda: view.control.closing)
        heartbeat()
        assert view.window is None
        release.set()
        assert startup.wait(3)
        QApplication.instance().processEvents()
        assert view.window is None and not view.progress.isVisible()
        assert not any(kind == "shown" for kind, _ in setup.notices)
        assert "closed" in setup.calls
        assert "register" not in setup.calls
        with pytest.raises(CancelledError):
            startup.ready.result()
        with SessionJournal(setup.args.launch_dir, setup.args.session):
            pass
    finally:
        release.set()


def provider_config():
    return {
        "provider": "anthropic",
        "model": "startup-model",
        "base_url": "https://gateway.invalid/v1",
        "api_key_env": "STARTUP_TEST_KEY",
    }


def test_missing_credential_is_requested_on_qt_after_background_validation(setup, monkeypatch):
    monkeypatch.delenv("STARTUP_TEST_KEY", raising=False)
    monkeypatch.setattr(
        startup_module, "resolve_provider_config", lambda *a, **kw: provider_config()
    )
    calls = []

    def enter(*args, **kwargs):
        assert threading.get_ident() == setup.owner
        assert kwargs["password"] is True
        calls.append("prompt")
        heartbeat()
        return " memory-only-secret ", True

    monkeypatch.setattr("sico_ui.credentials.ask_text", enter)
    startup, view, _ = setup.create()
    wait_until(lambda: view.window is not None)
    startup.ready.result()
    assert calls == ["prompt"]
    assert startup_backend(startup).controller.loop.provider.api_key == "memory-only-secret"
    assert "STARTUP_TEST_KEY" not in os.environ
    events = startup_backend(startup).controller.journal.directory / "events.jsonl"
    assert "memory-only-secret" not in events.read_text()


def test_ready_resources_remain_owned_until_session_window_closes(setup, monkeypatch):
    created, closed = [], []
    create_backend = startup_module.create_backend
    provider = ScriptedProvider([
        [ModelTurn("", (ToolCall("read-source", "get_context", {}),))],
        [TextDelta("The startup resources still work"),
         ModelTurn("The startup resources still work")],
    ])
    monkeypatch.setattr(startup_module, "provider_from_config", lambda *a, **kw: provider)

    def tracked_backend(*args, **kwargs):
        loop = create_backend(*args, **kwargs)

        def close():
            assert threading.get_ident() != setup.owner
            assert not loop.active and loop.journal._fd >= 0
            assert "closed" not in setup.calls
            closed.append(loop)

        loop.close = close
        created.append(loop)
        return loop

    monkeypatch.setattr(startup_module, "create_backend", tracked_backend)
    startup, view, _ = setup.create()
    wait_until(lambda: view.window is not None)
    wait_until(lambda: not view._initial_timer.isActive())
    settle(view.window)
    assert startup.ready.done()
    assert "closed" not in setup.calls
    assert not view.window.api.is_closing(view.window.page.session)
    assert not closed and not startup.wait(0)
    view.window.input.setPlainText("Read the source after bootstrap")
    view.window.actions.submit()
    settle(view.window)
    assert len(provider.requests) == 2
    assert "get_context" in setup.calls
    journal = startup_backend(startup).controller.journal
    assert journal.state.task["status"] == "completed"
    assert not closed and not startup.wait(0)

    view.close()
    view.close()
    assert startup.wait(3)
    assert closed == created
    assert setup.calls.count("closed") == 1
    assert setup.calls.count("release") == 1
    assert journal._fd == journal._lock == -1
    with SessionJournal(setup.args.launch_dir, setup.args.session) as reopened:
        assert reopened.state.task["status"] == "completed"


@pytest.mark.parametrize("stage", ["controller", "sessions"])
def test_failed_session_construction_closes_backend_and_journal(setup, monkeypatch, stage):
    closed = []
    create_backend = startup_module.create_backend

    def tracked_backend(*args, **kwargs):
        loop = create_backend(*args, **kwargs)

        def close():
            assert threading.get_ident() != setup.owner
            closed.append(loop)

        loop.close = close
        return loop

    def unavailable(*args, **kwargs):
        raise OSError("Session construction unavailable")

    monkeypatch.setattr(startup_module, "create_backend", tracked_backend)
    monkeypatch.setattr(
        startup_module, "SessionController" if stage == "controller" else "DesktopSessions",
        unavailable,
    )
    monkeypatch.setattr("sico_ui.startup.notice", lambda *a, **kw: None)
    startup, view, _ = setup.create()
    wait_until(lambda: view.control.closing)
    assert startup.wait(3)
    assert len(closed) == 1 and closed[0].journal._fd == -1
    assert view.window is None
    with SessionJournal(setup.args.launch_dir, setup.args.session):
        pass


@pytest.mark.parametrize("shutdown", ["command", "eof", "cancel"])
def test_shutdown_while_credential_dialog_is_open_never_builds_a_session(
    setup,
    monkeypatch,
    shutdown,
):
    monkeypatch.delenv("STARTUP_TEST_KEY", raising=False)
    monkeypatch.setattr(
        startup_module, "resolve_provider_config", lambda *a, **kw: provider_config()
    )
    startup, view, reader = setup.create()
    seen = []
    timer = QTimer()

    def cancel_prompt():
        dialog = QApplication.activeModalWidget()
        if not isinstance(dialog, SiPrompt):
            return
        seen.append(dialog.field.echoMode())
        timer.stop()
        if shutdown == "command":
            reader.lines.put({"kind": "shutdown"})
        elif shutdown == "eof":
            reader.closed.set()
        else:
            dialog.reject()

    timer.timeout.connect(cancel_prompt)
    timer.start(5)
    try:
        wait_until(lambda: view.control.closing)
        assert startup.wait(3)
        assert seen == [QLineEdit.Password]
        assert view.window is None
        assert "register" not in setup.calls
    finally:
        timer.stop()


def test_invalid_configuration_is_presented_on_qt_without_prompting_for_a_key(setup, monkeypatch):
    def invalid(*args, **kwargs):
        assert threading.get_ident() != setup.owner
        raise ValueError("Invalid configuration")

    def unexpected(*args, **kwargs):
        pytest.fail("Invalid configuration requested a credential")

    messages = []

    def critical(*args, **kwargs):
        assert threading.get_ident() == setup.owner
        messages.append(args)

    monkeypatch.setattr(startup_module, "resolve_provider_config", invalid)
    monkeypatch.setattr("sico_ui.credentials.ask_text", unexpected)
    monkeypatch.setattr("sico_ui.startup.notice", critical)
    startup, view, _ = setup.create()
    wait_until(lambda: view.control.closing)
    assert startup.wait(3)
    assert view.exit_code == 2 and len(messages) == 1
    assert messages[0][1].endswith("模型配置")
    assert ("startup_failed", {"phase": "config"}) in setup.notices
    assert view.window is None


def test_window_refusal_is_relayed_without_a_second_dialog(setup, monkeypatch):
    from sico.service.window_lease import WindowAlreadyOpen

    def refused(_startup, _resources):
        raise StartupFailure("window", WindowAlreadyOpen())

    monkeypatch.setattr(startup_module.DesktopStartup, "_prepare", refused)
    messages = []
    monkeypatch.setattr("sico_ui.startup.notice",
                        lambda *args, **kwargs: messages.append(args))
    startup, view, _ = setup.create()
    wait_until(lambda: view.control.closing)
    assert startup.wait(3)
    assert messages == []
    assert ("startup_failed", {"phase": "window"}) in setup.notices
    with pytest.raises(StartupFailure, match="已有一个 SiCo 窗口在运行"):
        startup.ready.result()
    assert view.window is None


def test_context_identity_mismatch_never_opens_journal_or_session(setup, monkeypatch):
    monkeypatch.setattr(
        setup.broker,
        "context",
        lambda *a, **kw: BoundContext("instance", "other-generation", "target", {}),
    )
    monkeypatch.setattr("sico_ui.startup.notice", lambda *args, **kwargs: None)
    startup, view, _ = setup.create()
    wait_until(lambda: view.control.closing)
    assert startup.wait(3)
    with pytest.raises(StartupFailure, match="does not match"):
        startup.ready.result()
    assert "register" not in setup.calls
    assert view.window is None


def test_registration_timeout_keeps_qt_responsive_and_exits_cleanly(setup, monkeypatch):
    messages = []
    monkeypatch.setattr("sico_ui.startup.notice",
                        lambda *args, **kwargs: messages.append(args))
    startup, view, reader = setup.create(registration_timeout=0.2)
    reader.lines.get_nowait()
    heartbeat()
    wait_until(lambda: view.control.closing)
    assert startup.wait(3)
    assert view.exit_code == 2 and len(messages) == 1
    assert ("startup_failed", {"phase": "registration"}) in setup.notices
    assert not setup.calls


@pytest.mark.parametrize("shutdown", ["command", "view_close"])
def test_ready_result_cannot_open_window_after_shutdown(setup, shutdown):
    startup, view, reader = setup.create()
    view.configuration.timer.stop()
    view.ready.timer.stop()
    view.control.poll()
    result = startup.ready.result(3)
    if shutdown == "command":
        reader.lines.put({"kind": "shutdown"})
        view.control.poll()
    else:
        view.close()
    view._ready(result)
    assert view.window is None
    assert startup.wait(3)
    assert "closed" in setup.calls
