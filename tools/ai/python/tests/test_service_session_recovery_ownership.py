"""Recovery bridge ownership and cancellation boundaries."""

from __future__ import annotations

import threading
from contextlib import ExitStack
from types import SimpleNamespace

import pytest
from session_recovery_boundary_fixtures import (
    CURRENT,
    OTHER,
    SERVICE,
    VERSION,
    Bridge,
    facts,
    recovery,
)
from sico.core.contracts import BoundContext
from sico.service import session_recovery


def test_open_hands_the_selected_bridge_to_the_owner(monkeypatch):
    value = recovery()
    bridge = Bridge(OTHER, {"bridge_id": "b"})
    durable = facts()
    controller = value._open(
        SimpleNamespace(closed=threading.Event()), "op", "old", durable,
        {"KEY": "secret"}, selected=(bridge, OTHER))
    assert controller == "controller"
    assert bridge.exited == 0
    assert value.owner.opened[0][0:3] == ("old", {"bridge_id": "b"}, OTHER)
    kwargs = value.owner.opened[0][3]
    assert kwargs["environment"] == {"KEY": "secret"}
    assert kwargs["recovery"] == {"operation_id": "op", "service_id": SERVICE,
                                  "version": VERSION}
    assert kwargs["previous_context"] == CURRENT
    assert value.owner.owned and value.owner.owned[0][0] == "controller"


def test_open_rolls_back_bridges_on_cancel_or_owner_failure():
    value = recovery()
    bridge = Bridge()
    connection = SimpleNamespace(closed=threading.Event())
    connection.closed.set()
    with pytest.raises(ValueError, match="取消"):
        value._open(connection, "op", "old", facts(), selected=(bridge, CURRENT))
    assert bridge.exited == 1
    assert not value.owner.opened

    bridge = Bridge()
    value.owner.open = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        RuntimeError("open failed"))
    with pytest.raises(RuntimeError, match="open failed"):
        value._open(SimpleNamespace(closed=threading.Event()), "op", "old",
                    facts(), selected=(bridge, CURRENT))
    assert bridge.exited == 1


def test_open_continue_uses_current_host_and_recovery_environment(monkeypatch):
    value = recovery()
    bridge = Bridge(OTHER)
    durable = facts()
    seen = {}
    monkeypatch.setattr("sico.service.recovery_environment.recovery_environment",
                        lambda _owner, _key, _snapshot: {"RECOVERED": "1"})
    monkeypatch.setattr("sico.service.continuation_host.current_host",
                        lambda owner, preferred: (bridge, OTHER))

    def open_(_key, descriptor, context, **kwargs):
        seen.update(descriptor=descriptor, context=context, **kwargs)
        return "controller"

    value.owner.open = open_
    assert value._open(SimpleNamespace(closed=threading.Event()), "op", "old",
                       durable, continue_chat=True,
                       preferred="preferred") == "controller"
    assert seen["environment"] == {"RECOVERED": "1"}
    assert seen["context"] == OTHER
    assert seen["background"] is None


def test_open_rejects_a_closed_recovery_entry_before_opening():
    value = recovery()
    value._closed.set()
    with pytest.raises(ValueError, match="已关闭"):
        value._open(SimpleNamespace(closed=threading.Event()), "op", "old", facts())
    assert not value.owner.opened


def test_open_uses_the_normal_bridge_when_no_current_host_is_selected():
    value = recovery()
    bridge = Bridge()
    value._bridge = lambda *_args: bridge
    controller = value._open(SimpleNamespace(closed=threading.Event()), "op", "old",
                             facts())
    assert controller == "controller"
    assert value.owner.opened[0][0:3] == ("old", {}, CURRENT)
    assert value.owner.opened[0][3]["bridge"] is bridge


def test_chat_bridge_falls_back_to_unavailable_without_closing_success(monkeypatch):
    from sico.transport import unavailable_bridge

    class Unavailable:
        pass

    monkeypatch.setattr(unavailable_bridge, "UnavailableBridge", Unavailable)
    value = recovery()
    rollback, peers = ExitStack(), ExitStack()
    events = []
    rollback.callback(lambda: events.append("rollback"))
    peers.callback(lambda: events.append("peers"))
    monkeypatch.setattr(value, "_bridge", lambda *_args: "connected")
    assert value._chat_bridge(facts(), rollback, peers) == "connected"
    assert events == []
    rollback.close()
    peers.close()
    assert events == ["rollback", "peers"]

    rollback, peers = ExitStack(), ExitStack()
    sandbox = []
    rollback.callback(lambda: sandbox.append("rollback"))
    peers.callback(lambda: sandbox.append("peers"))
    monkeypatch.setattr(value, "_bridge",
                        lambda *_args: (_ for _ in ()).throw(ValueError("offline")))
    assert isinstance(value._chat_bridge(facts(), rollback, peers), Unavailable)
    assert sandbox == ["rollback", "peers"]

    rollback, peers = ExitStack(), ExitStack()
    assert isinstance(value._chat_bridge(
        facts(ends=({"kind": "session.end_requested"},)), rollback, peers), Unavailable)


def test_bridge_builds_a_simulated_context(monkeypatch):
    value = recovery()

    class RecordingBridge(Bridge):
        def __init__(self):
            super().__init__()
            self.closed = []

        def __exit__(self, *_args):
            self.closed.append(True)
            return False

    broker = RecordingBridge()
    peer = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(session_recovery, "ContextBroker", lambda _window: broker)
    monkeypatch.setattr(session_recovery, "DemoPeer", lambda _broker, _context: peer)
    durable = facts(context=BoundContext("i", "g", "t", {"source": "simulated"}),
                    snapshot={"bridge_identity": {}})
    rollback, peers = ExitStack(), ExitStack()
    assert value._bridge(durable, rollback, peers) is broker
    rollback.close()
    assert broker.closed == [True]
    peers.close()


@pytest.mark.parametrize("failure", [ConnectionError("closed"), TimeoutError("late")])
def test_bridge_reports_discovery_failures_as_read_only(monkeypatch, failure):
    value = recovery()
    identity = {"bridge_id": "b" * 32, "router_id": "r" * 32}
    durable = facts(bridge_identity=identity, snapshot={
        "bridge_identity": identity, "provider_config": None,
        "environment": {}, "background": None,
    })
    monkeypatch.setattr(session_recovery.BridgeClient, "discover",
                        staticmethod(lambda *_args, **_kwargs: (_ for _ in ()).throw(
                            failure)))
    with pytest.raises(ValueError, match="已断开"):
        value._bridge(durable, ExitStack(), ExitStack())


def test_bridge_requires_stable_identity_and_context(monkeypatch):
    value = recovery()
    identity = {"bridge_id": "b" * 32, "router_id": "r" * 32}
    durable = facts(bridge_identity=identity, snapshot={
        "bridge_identity": identity, "provider_config": None,
        "environment": {}, "background": None,
    })
    with pytest.raises(ValueError, match="宿主身份"):
        value._bridge(facts(bridge_identity={"bridge_id": "b" * 32},
                            snapshot=durable.snapshot),
                      ExitStack(), ExitStack())

    bridge = Bridge(OTHER, dict(identity, bridge_id="wrong"))
    monkeypatch.setattr(session_recovery.BridgeClient, "discover",
                        staticmethod(lambda *_args, **_kwargs: bridge))
    with pytest.raises(ValueError, match="已变化"):
        value._bridge(durable, ExitStack(), ExitStack())

    bridge = Bridge(OTHER, identity)
    monkeypatch.setattr(session_recovery.BridgeClient, "discover",
                        staticmethod(lambda *_args, **_kwargs: bridge))
    with pytest.raises(ValueError, match="目标"):
        value._bridge(durable, ExitStack(), ExitStack())


def test_bridge_returns_only_an_exactly_matching_discovery(monkeypatch):
    value = recovery()
    identity = {"bridge_id": "b" * 32, "router_id": "r" * 32}
    bridge = Bridge(CURRENT, identity)
    durable = facts(bridge_identity=identity, snapshot={
        "bridge_identity": identity, "provider_config": None,
        "environment": {}, "background": None,
    })
    monkeypatch.setattr(session_recovery.BridgeClient, "discover",
                        staticmethod(lambda *_args, **_kwargs: bridge))
    rollback = ExitStack()
    assert value._bridge(durable, rollback, ExitStack()) is bridge
    assert bridge.entered == 1
    rollback.close()
    assert bridge.exited == 1


def test_close_sets_the_cancellation_flag_and_joins_the_scan():
    value = recovery()
    joined = []
    value._cleanup = SimpleNamespace(
        start=lambda: SimpleNamespace(wait=lambda timeout: ("waited", timeout)))
    assert value.close(3) == ("waited", 3)
    assert value._closed.is_set()
    value._thread = SimpleNamespace(join=lambda: joined.append(True))
    value._join()
    assert joined == [True]
