"""Demo/connect GUI startup keeps Qt alive before configuration, I/O and backend creation."""

import json
import socket
import threading
from concurrent.futures import CancelledError
from types import SimpleNamespace

import agent_local_harness as local_module
import agent_startup_harness as startup_module
import pytest
from agent_gui_wait import settle, wait_until
from agent_local_startup_fixtures import local_setup as local_setup
from agent_startup_fixture import startup_backend
from agent_worker_fixtures import heartbeat

from sico.core.contracts import BoundContext, RunState
from sico.service.startup_result import StartupFailure
from sico.storage.journal import SessionJournal
from sico.storage import journal_recovery
from sico.transport.framing import ProtocolError
from sico_ui.window import AssistantWindow


@pytest.mark.parametrize("command", ["demo", "connect"])
@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("phase", ["config", "history", "bridge", "context", "backend", "sessions"])
def test_slow_local_startup_preserves_progress_cancel_and_resource_ownership(
    local_setup, monkeypatch, command, cancel, phase,
):
    entered, release = threading.Event(), threading.Event()
    target, name = {
        "config": (startup_module, "resolve_provider_config"),
        "history": (journal_recovery, "writable_index"),
        "bridge": (local_module, "ContextBroker"),
        "context": ((local_module, "DemoPeer") if command == "demo"
                    else (local_module.LocalStartup, "_context")),
        "backend": (startup_module, "create_backend"),
        "sessions": (startup_module, "DesktopSessions"),
    }[phase]
    original = getattr(target, name)

    def slow(*args, **kwargs):
        assert threading.get_ident() != local_setup.owner
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(target, name, slow)
    startup, view = local_setup.create(command)
    try:
        wait_until(entered.is_set)
        assert view.progress.isVisible() and view.window is None
        heartbeat(view.progress)
        assert not release.is_set()
        if cancel:
            view.progress.cancel.click()
            assert view.control.closing and not view.progress.isVisible()
            heartbeat(view.progress)
            assert not release.is_set()
        release.set()
        if cancel:
            assert startup.wait(5)
            view.ready.poll()
            assert view.window is None
            with pytest.raises(CancelledError):
                startup.ready.result()
        else:
            wait_until(lambda: view.window is not None)
            wait_until(lambda: not view._initial_timer.isActive())
            settle(view.window)
            assert view.window.input.toPlainText() == "draft"
            assert not hasattr(view.window, "controller") and not hasattr(view.window, "sessions")
            assert not startup.wait(0) and not local_setup.calls
            if command == "demo":
                assert local_setup.loops[0].state.context.snapshot["source"] == "simulated"
            else:
                assert "sicoConnect(" in startup.instructions
                descriptor = json.loads((local_setup.journals[0].directory / "connection.json")
                                        .read_text())
                assert descriptor["token"] not in startup.instructions
            view.close()
            view.close()
            assert startup.wait(5)
        for kind, values in (("journal", local_setup.journals), ("broker", local_setup.brokers),
                             ("backend", local_setup.loops)):
            assert all(local_setup.calls.count((kind, value)) == 1 for value in values)
        assert all(journal._fd == journal._lock == -1 for journal in local_setup.journals)
    finally:
        release.set()


@pytest.mark.parametrize("command", ["demo", "connect"])
@pytest.mark.parametrize("cancel", [False, True])
def test_missing_local_credential_uses_qt_prompt_and_stays_out_of_journal(
    local_setup, monkeypatch, command, cancel,
):
    key, calls = "FB05_TEST_KEY", []
    monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(startup_module, "resolve_provider_config", lambda *a, **kw: {
        "provider": "anthropic", "model": "local-test", "base_url": "https://example.invalid/v1",
        "api_key_env": key,
    })

    def credential(*args, **kwargs):
        assert threading.get_ident() == local_setup.owner and kwargs["password"] is True
        calls.append(True)
        return "ephemeral-test-key", not cancel

    monkeypatch.setattr("sico_ui.credentials.ask_text", credential)
    startup, view = local_setup.create(command)
    wait_until(lambda: view.control.closing if cancel else view.window is not None)
    assert calls == [True]
    if cancel:
        assert startup.wait(5) and not local_setup.loops
    else:
        assert startup_backend(startup).controller.loop.provider.api_key == "ephemeral-test-key"
        events = local_setup.journals[0].directory / "events.jsonl"
        assert "ephemeral-test-key" not in events.read_text()


@pytest.mark.parametrize("stop", ["cancel", "timeout"])
@pytest.mark.parametrize("partial", [b"", b"\0\0", b"\0\0\0\x40{"])
def test_connect_wait_and_partial_registration_release_resources(local_setup, monkeypatch,
                                                                stop, partial):
    errors = []
    monkeypatch.setattr("sico_ui.startup.notice", lambda *args, **kwargs: errors.append(args))
    startup, view = local_setup.create("connect", attach=False, timeout=0.5)
    wait_until(lambda: bool(startup.instructions))
    view._progress()
    assert view.progress.instructions.isVisible()
    client = socket.create_connection(local_setup.brokers[0].address, timeout=1)
    try:
        if partial:
            client.sendall(partial)
        heartbeat(view.progress)
        if stop == "cancel":
            view.progress.cancel.click()
        wait_until(lambda: view.control.closing)
        assert startup.wait(3) and view.window is None
        assert len(errors) == (stop == "timeout")
        assert local_setup.journals[0]._fd == -1
        assert not local_setup.loops
    finally:
        client.close()


@pytest.mark.parametrize("command", ["demo", "connect"])
def test_local_ready_after_close_never_reopens_window(local_setup, command):
    startup, view = local_setup.create(command)
    view.ready.timer.stop()
    result = startup.ready.result(5)
    view.close()
    view._ready(result)
    assert view.window is None and startup.wait(5)


@pytest.mark.parametrize("command", ["demo", "connect"])
def test_local_window_construction_failure_retires_backend(local_setup, monkeypatch, command):
    import sico_ui.startup as ui_module

    def fail(*args, **kwargs):
        raise RuntimeError("window failed")

    monkeypatch.setattr(ui_module, "AssistantWindow", fail)
    monkeypatch.setattr("sico_ui.startup.notice", lambda *args, **kwargs: None)
    startup, view = local_setup.create(command)
    wait_until(lambda: view.control.closing)
    assert view.exit_code == 2 and startup.wait(5)
    assert local_setup.calls.count(("backend", local_setup.loops[0])) == 1
    assert local_setup.journals[0]._fd == -1


def test_connect_refuses_existing_real_session_without_rebinding(local_setup, monkeypatch):
    with SessionJournal(local_setup.root, "local") as journal:
        journal.append("session.created", {}, RunState(BoundContext("old", "old", "bound", {})))
    monkeypatch.setattr("sico_ui.startup.notice", lambda *args, **kwargs: None)
    startup, view = local_setup.create("connect", attach=False)
    wait_until(lambda: view.control.closing)
    with pytest.raises(StartupFailure, match="Rebinding"):
        startup.ready.result()
    assert startup.wait(5) and not startup.instructions and not local_setup.loops


@pytest.mark.parametrize("invalid", [object(), SimpleNamespace(journal=object())])
def test_production_window_rejects_unprepared_backend(invalid):
    with pytest.raises(TypeError, match="prepared FrontendSession"):
        AssistantWindow(invalid)


@pytest.mark.parametrize("wrong", ["instance", "generation"])
def test_connect_rejects_wrong_source_before_accepting_valid_registration(local_setup, wrong):
    startup, view = local_setup.create("connect", attach=False)
    wait_until(lambda: bool(startup.instructions))
    broker = local_setup.brokers[0]
    instance, generation = broker.expected_identity
    with pytest.raises((EOFError, OSError, ProtocolError)):
        local_setup.connect(broker, "wrong" if wrong == "instance" else instance,
                            generation, wrong=wrong == "generation")
    assert not broker.peers and view.window is None and not local_setup.loops
    heartbeat(view.progress)
    expected = local_setup.connect(broker, instance, generation)
    wait_until(lambda: view.window is not None)
    assert startup_backend(startup).controller.current == expected


def test_connect_context_result_must_match_requested_identity(local_setup, monkeypatch):
    def wrong(*args, **kwargs):
        return BoundContext("foreign", "foreign", "bound", {})

    monkeypatch.setattr(local_module.ContextBroker, "context", wrong)
    monkeypatch.setattr("sico_ui.startup.notice", lambda *args, **kwargs: None)
    startup, view = local_setup.create("connect", attach=False)
    wait_until(lambda: view.control.closing)
    with pytest.raises(StartupFailure, match="source does not match"):
        startup.ready.result()
    assert startup.wait(5) and not local_setup.loops


@pytest.mark.parametrize("simulated", [False, True])
def test_demo_resume_preserves_source_and_refuses_real_journal(local_setup, monkeypatch, simulated):
    context = BoundContext("saved-instance", "saved-generation", "bound", {
        "source": "simulated" if simulated else "real", "valid": True,
    })
    with SessionJournal(local_setup.root, "local") as journal:
        journal.append("session.created", {}, RunState(context))
    monkeypatch.setattr("sico_ui.startup.notice", lambda *args, **kwargs: None)
    startup, view = local_setup.create("demo")
    wait_until(lambda: view.window is not None or view.control.closing)
    if simulated:
        assert startup_backend(startup).controller.current == context
    else:
        with pytest.raises(StartupFailure, match="Cannot resume a real target"):
            startup.ready.result()
        assert startup.wait(5) and not local_setup.loops
