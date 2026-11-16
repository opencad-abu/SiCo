"""Execute actual storage, wire and startup paths under injected GUI thread guards."""

import socket
import threading

import agent_startup_harness as startup_module
import pytest
from agent_gui_wait import settle, wait_until
from agent_local_startup_fixtures import local_setup as local_setup
from agent_thread_guard import ThreadGuard
from agent_worker_fixtures import heartbeat
from agent_worker_fixtures import window as window

from sico.service.inbox import SessionInbox
from sico.storage.journal import SessionJournal
from sico.storage import journal_recovery
from sico.transport.framing import Connection


@pytest.mark.parametrize("target,name", [
    (SessionJournal, "prepare_replay"), (SessionJournal, "committed_batch"),
    (SessionJournal, "_append"), (SessionJournal, "artifact_text"),
    (SessionInbox, "_save"), (Connection, "send"), (Connection, "receive"),
    (startup_module, "resolve_provider_config"), (startup_module, "create_backend"),
])
def test_wrong_thread_fails_before_storage_or_transport(target, name, monkeypatch):
    guarded = ThreadGuard(monkeypatch).protect(target, name)
    with pytest.raises(AssertionError, match="Blocking boundary on GUI thread"):
        guarded()


def test_live_replay_and_durable_acceptance_use_guarded_boundaries(window, monkeypatch):
    guard = ThreadGuard(monkeypatch)
    for target, names in (
        (SessionJournal, ("prepare_replay", "committed_batch", "_append")),
        (SessionInbox, ("_save",)),
    ):
        for name in names:
            guard.protect(target, name)
    window.controller.paused = True
    window.input.setPlainText("guarded durable input")
    window.actions.submit()
    settle(window, tasks=False)
    window.page.activate_session("history")
    wait_until(lambda: not window.page.opening)
    window.page.activate_session("initial")
    settle(window, tasks=False)
    names = {name for name, _ in guard.calls}
    assert {"prepare_replay", "committed_batch", "_save"} <= names
    assert len(window.controller.inbox.pending) == 1


def test_actual_socket_wait_keeps_gui_heartbeat(window, monkeypatch):
    guard = ThreadGuard(monkeypatch)
    guard.protect(Connection, "send")
    guard.protect(Connection, "receive")
    left, right = socket.socketpair()
    client, peer = Connection(left), Connection(right)
    entered = threading.Event()

    def receive():
        entered.set()
        return client.receive()

    future = window.sessions.data._request(receive)
    try:
        assert entered.wait(2)
        heartbeat(window)
        assert not future.done()
        sender = threading.Thread(target=peer.send, args=({"received": True},))
        sender.start()
        assert future.result(2) == {"received": True}
        sender.join(2)
        assert not sender.is_alive()
        assert {name for name, _ in guard.calls} == {"send", "receive"}
    finally:
        client.close()
        peer.close()


@pytest.mark.parametrize("command", ["demo", "connect"])
def test_local_startup_executes_real_boundaries_off_qt(local_setup, monkeypatch, command):
    guard = ThreadGuard(monkeypatch)
    for target, names in (
        (startup_module, ("resolve_provider_config", "create_backend")),
        (journal_recovery, ("writable_index",)),
        (SessionJournal, ("_append", "prepare_replay", "committed_batch")),
        (Connection, ("send", "receive")),
    ):
        for name in names:
            guard.protect(target, name)
    startup, view = local_setup.create(command)
    wait_until(lambda: view.window is not None)
    settle(view.window)
    assert {"resolve_provider_config", "create_backend", "writable_index", "send", "receive",
            "prepare_replay", "committed_batch"} <= {name for name, _ in guard.calls}
    view.close()
    assert startup.wait(5)
