# Original test bodies retain their imports and Qt lifetime references.
# ruff: noqa: F401, F841, I001
"""agent native binding contract lifecycle contracts."""

from __future__ import annotations

import pytest
from agent_native_binding_contract_support import created_message, dispatch, output
from test_agent_session_bindings import target

from sico.core.contracts import BoundContext, CircuitCallError
from sico.demo import DemoPeer
from sico.transport.broker import ContextBroker, skill_call_context
from sico.transport.instance_bridge import InstanceBridge


@pytest.mark.parametrize(
    "verified,creator,automatic", [(True, True, True), (False, True, False), (True, False, False)]
)
def test_verified_origin_controls_timeout_and_close_retains_resource(
    tmp_path, verified, creator, automatic
):
    ciw = target()
    with InstanceBridge(tmp_path, ciw) as bridge:
        peer = DemoPeer(bridge.broker, ciw)
        try:
            broker = bridge.broker
            broker.register_session("owner", ciw)
            identity = dispatch(broker, "aiCreateExecute" if creator else "aiSimRun")
            message = created_message(identity)
            message["verified"] = verified
            proposal = bridge.native_binding_event(message)
            assert (proposal["timeout_policy"] == "add_and_select") is automatic
            assert bridge.native_binding_event(message) == proposal
            broker.resolve_binding("owner", proposal["event_id"], "add_and_select")
            assert broker.binding_snapshot("owner")["default_target_id"] == "created"
            bridge.native_binding_event(
                dict(kind="binding.native_closed", context=output().record())
            )
            snapshot = broker.binding_snapshot("owner")
            assert snapshot["default_target_id"] == "ciw" and snapshot["invalidated"]["created"]
            with pytest.raises(CircuitCallError) as closed:
                broker.bindings.require("owner", output())
            assert closed.value.code == "binding_target_closed"
            with pytest.raises(CircuitCallError):
                broker.register_session("other", output())
            broker.release_binding("owner", output())
            assert not broker.binding_snapshot("owner")["reservations"]
            assert not broker.bindings.owners
        finally:
            peer.close()


@pytest.mark.parametrize("change", ["lifetime", "path", "target", "origin"])
def test_invalid_native_event_keeps_durable_evidence_without_losing_channel(tmp_path, change):
    ciw = target()
    with InstanceBridge(tmp_path, ciw) as bridge:
        peer = DemoPeer(bridge.broker, ciw)
        try:
            bridge.broker.register_session("owner", ciw)
            identity = dispatch(bridge.broker)
            message = created_message(identity)
            if change == "lifetime":
                message["binding_id"] = "foreign"
            elif change == "path":
                message["context"]["snapshot"]["library_path"] = "/different"
            elif change == "target":
                message["context"]["snapshot"]["cellview"]["cell"] = "Foreign"
            else:
                message["request_id"] = "b" * 32
            assert bridge.native_binding_event(message) is None
            assert not bridge.broker.binding_snapshot("owner")["proposals"]
            evidence = bridge.broker.binding_events.events[-1]
            assert evidence["kind"] == "binding.native_event_rejected"
            assert evidence["payload"]["native_event"] == message
            assert "binding.native_event_rejected" in bridge.binding_journal.path.read_text()
            with skill_call_context(session_id="owner"):
                assert bridge.broker.get_context(ciw)["valid"]
        finally:
            peer.close()


def test_closed_target_cannot_be_registered_again_even_after_owner_releases(tmp_path):
    ciw = target()
    with InstanceBridge(tmp_path, ciw) as bridge:
        broker = bridge.broker
        peer = DemoPeer(broker, ciw)
        try:
            broker.register_session("owner", ciw)
            identity = dispatch(broker)
            proposal = bridge.native_binding_event(created_message(identity))
            broker.resolve_binding("owner", proposal["event_id"], "add_only")
            bridge.native_binding_event(
                dict(kind="binding.native_closed", context=output().record())
            )
            broker.release_binding("owner", output())
            with pytest.raises(CircuitCallError) as closed:
                broker.register_session("new", output())
            assert closed.value.code == "binding_target_closed"
            # New observation of the same numeric window belongs to a new captured identity.
            fresh = BoundContext(
                "instance", "g", "reopened", {**output().snapshot, "window_ref": "fresh"}
            )
            broker.register_target(fresh)
            broker.register_session("new", fresh)
            assert broker.bindings.require("new", fresh)
        finally:
            peer.close()


def test_late_close_after_target_release_is_terminal_and_duplicate_close_is_idempotent(tmp_path):
    ciw = target()
    with InstanceBridge(tmp_path, ciw) as bridge:
        peer = DemoPeer(bridge.broker, ciw)
        try:
            broker = bridge.broker
            broker.register_target(output())
            broker.register_session('owner', output())
            message = dict(kind='binding.native_closed', context=output().record())
            bridge.native_binding_event(message)
            before = broker.binding_snapshot('owner')
            bridge.native_binding_event(message)
            assert broker.binding_snapshot('owner') == before
            broker.release_session('owner')
            broker.release_target(output())
            bridge.native_binding_event(message)
            assert not any(row['kind'] == 'binding.native_event_rejected'
                           for row in broker.binding_events.events)
        finally:
            peer.close()


def test_close_still_rejects_changed_live_identity():
    ciw = target()
    with ContextBroker(2) as broker:
        peer = DemoPeer(broker, ciw)
        try:
            broker.register_target(output())
            changed = output().record()
            changed['snapshot']['window'] = 'window:999'
            with pytest.raises(ValueError, match='identity changed'):
                broker.native_bindings.event(dict(kind='binding.native_closed', context=changed))
            assert not broker.native_bindings.invalidated
        finally:
            peer.close()
