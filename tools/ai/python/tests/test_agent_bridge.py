"""Offline acceptance for the process-owned instance bridge, without design I/O."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from sico.core.contracts import BoundContext, NeedsReconcile
from sico.demo import DemoPeer
from sico.transport.broker import ContextBroker, skill_call_context
from sico.transport.framing import VERSION, Connection


def bound(generation="g", target="bound"):
    return BoundContext("bridge-instance", generation, target,
                        {"source": "simulated", "valid": True})


def connect_peer(broker, context):
    connection = Connection(socket.create_connection(broker.address, timeout=3))
    connection.send({"protocol": VERSION, "kind": "hello", "token": broker.token,
                     "instance_id": context.instance_id, "generation": context.generation,
                     "contexts": [context.record()]})
    assert connection.receive()["kind"] == "welcome"
    return connection


def reply(connection, request, value):
    connection.send({"protocol": VERSION, "kind": "response", "id": request["id"],
                     "generation": request["generation"], "ok": True,
                     "result": {"valid": True, "value": value}})


def test_independent_processes_discover_one_bridge_and_release_only_their_sessions(tmp_path):
    from sico.transport.bridge_client import BridgeClient
    from sico.transport.instance_bridge import InstanceBridge

    with InstanceBridge(tmp_path, bound()) as bridge:
        peer = DemoPeer(bridge.broker, bound())
        try:
            with BridgeClient.discover(tmp_path, bound()) as first:
                first.register_session("session-a", bound())
                code = """
import json, sys
from sico.core.contracts import BoundContext
from sico.transport.bridge_client import BridgeClient
c = BoundContext.from_record(json.loads(sys.argv[2]))
with BridgeClient.discover(sys.argv[1], c) as client:
    print(json.dumps(client.register_session('session-b', c)), flush=True)
    print(json.dumps(client.get_context(c)), flush=True)
    input()
"""
                env = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path))
                child = subprocess.Popen([sys.executable, "-c", code, str(tmp_path),
                                          json.dumps(bound().record())], env=env,
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, text=True)
                try:
                    registered = json.loads(child.stdout.readline())
                    assert registered["bridge_id"] == first.bridge_id
                    assert registered["router_id"] == first.router_snapshot(bound())["router_id"]
                    assert registered["target_id"] == "bound"
                    assert json.loads(child.stdout.readline())["source"] == "simulated"
                    assert set(first.bridge_snapshot()["sessions"]) == {"session-a", "session-b"}
                    child.communicate("\n", timeout=5)
                    assert child.returncode == 0
                    assert first.get_context(bound())["valid"]
                    assert not first.router_snapshot(bound())["closed"]
                    assert first.release_session("session-a")
                finally:
                    if child.poll() is None:
                        child.kill()
                        child.communicate(timeout=5)
        finally:
            peer.close()


def test_clients_share_fifo_progress_cancel_and_unknown_quarantine(tmp_path):
    from sico.transport.bridge_client import BridgeClient
    from sico.transport.instance_bridge import InstanceBridge

    with InstanceBridge(tmp_path, bound()) as bridge:
        peer = connect_peer(bridge.broker, bound())
        with BridgeClient.discover(tmp_path, bound()) as first, \
                BridgeClient.discover(tmp_path, bound()) as second, ThreadPoolExecutor(2) as pool:
            first.register_session("a", bound())
            second.register_session("b", bound())
            queued = threading.Event()
            records = []

            def call(client, session):
                def progress(row):
                    records.append(row)
                    if row["state"] == "queued":
                        queued.set()
                with skill_call_context(session_id=session, progress=progress):
                    return client.get_context(bound())

            active = pool.submit(call, first, "a")
            request = peer.receive()
            waiting = pool.submit(call, second, "b")
            assert queued.wait(3)
            snapshot = second.router_snapshot(bound())
            assert snapshot["active_request"]["session_id"] == "a"
            row = snapshot["queue"][0]
            assert row["session_id"] == "b"
            assert second.cancel_request(bound(), row["request_id"], session_id="a") == "not_owner"
            assert second.cancel_request(bound(), row["request_id"], session_id="b") == "cancel_requested"
            with pytest.raises(Exception, match="cancelled"):
                waiting.result(3)
            peer.send({"protocol": VERSION, "kind": "response", "id": request["id"],
                       "generation": "g", "ok": False, "code": "skill_timeout"})
            with pytest.raises(Exception, match="skill_timeout"):
                active.result(3)
            with pytest.raises(NeedsReconcile, match="skill_blocked_unknown"):
                call(second, "b")
            peer.send({"protocol": VERSION, "kind": "skill_late_reply", "generation": "g",
                       "id": request["id"], "ok": True})
            # A read-only state check is sufficient to wait for the matching late receipt.
            import time
            deadline = time.monotonic() + 3
            while second.router_snapshot(bound())["unknown"]:
                assert time.monotonic() < deadline
                time.sleep(.01)
            completed = pool.submit(call, second, "b")
            next_request = peer.receive()
            assert next_request["id"] != request["id"]
            reply(peer, next_request, "second")
            assert completed.result(3) == {"valid": True, "value": "second"}
        peer.close()


def test_discovery_refuses_duplicate_owner_stale_record_and_wrong_identity(tmp_path):
    from sico.transport.bridge_client import BridgeClient
    from sico.transport.instance_bridge import InstanceBridge

    with InstanceBridge(tmp_path, bound()) as bridge:
        peer = DemoPeer(bridge.broker, bound())
        try:
            with pytest.raises((ValueError, RuntimeError), match="bridge"):
                InstanceBridge(tmp_path, bound())
            with BridgeClient.discover(tmp_path, bound()) as client:
                descriptor = dict(client.descriptor, generation="wrong")
                with pytest.raises((NeedsReconcile, ValueError, EOFError)):
                    BridgeClient(descriptor)
        finally:
            peer.close()
    with pytest.raises((NeedsReconcile, ValueError, RuntimeError), match="[Bb]ridge"):
        InstanceBridge(tmp_path, bound())


def test_old_peer_can_finish_after_same_generation_reconnect_without_closing_router():
    with ContextBroker() as broker, ThreadPoolExecutor(2) as pool:
        first = connect_peer(broker, bound())
        router = broker.registry.get(bound().instance_id, "g")
        result = pool.submit(broker.get_context, bound())
        request = first.receive()
        second = connect_peer(broker, bound())
        try:
            assert broker.registry.get(bound().instance_id, "g") is router
            reply(first, request, "old-request")
            assert result.result(3) == {"valid": True, "value": "old-request"}
            first.close()
            next_result = pool.submit(broker.get_context, bound())
            reply(second, second.receive(), "new-request")
            assert next_result.result(3) == {"valid": True, "value": "new-request"}
            assert not router.snapshot()["closed"]
        finally:
            first.close()
            second.close()


def test_retired_generation_cannot_replace_current_peer_or_restore_old_sessions():
    with ContextBroker() as broker:
        old = DemoPeer(broker, bound("old"))
        broker.register_session("old-session", bound("old"))
        new = DemoPeer(broker, bound("new"))
        try:
            with pytest.raises((EOFError, OSError)):
                stale = connect_peer(broker, bound("old"))
                stale.close()
            assert broker.context(bound().instance_id).generation == "new"
            assert not broker.bridge_snapshot()["sessions"]
            with pytest.raises(NeedsReconcile):
                broker.register_session("old-session", bound("old"))
            assert broker.get_context(bound("new"))["valid"]
        finally:
            old.close()
            new.close()


def test_target_release_waits_for_other_desktop_and_does_not_exhaust_registry(tmp_path):
    from sico.transport.bridge_client import BridgeClient
    from sico.transport.instance_bridge import InstanceBridge

    with InstanceBridge(tmp_path, bound()) as bridge:
        peer = DemoPeer(bridge.broker, bound())
        try:
            with BridgeClient.discover(tmp_path, bound()) as first, \
                    BridgeClient.discover(tmp_path, bound()) as second:
                context = bound(target="shared-target")
                first.register_target(context)
                second.register_session("second", context)
                first.release_target(context)
                assert second.get_context(context)["valid"]
                second.release_session("second")
                assert bridge.targets.get(context.target_id) is None
                for index in range(70):
                    context = bound(target="temporary-" + str(index))
                    first.register_target(context)
                    first.release_target(context)
                assert len(bridge.targets.records()) == 1
        finally:
            peer.close()


def test_desktop_teardown_after_bridge_exit_is_idempotent(tmp_path):
    from sico.transport.bridge_client import BridgeClient
    from sico.transport.instance_bridge import InstanceBridge

    with InstanceBridge(tmp_path, bound()) as bridge:
        peer = DemoPeer(bridge.broker, bound())
        client = BridgeClient.discover(tmp_path, bound())
        client.register_session("desktop", bound())
        bridge.close()
        peer.close()
        try:
            assert client.router_snapshot(bound()) is None
            assert client.release_session("desktop") is False
            client.release_target(bound())
        finally:
            client.close()


def test_demoted_peer_cannot_resolve_another_peers_unknown_request():
    import time

    with ContextBroker() as broker, ThreadPoolExecutor(1) as pool:
        first = connect_peer(broker, bound())
        second = connect_peer(broker, bound())
        try:
            pending = pool.submit(broker.get_context, bound())
            request = second.receive()
            second.send({"protocol": VERSION, "kind": "response", "id": request["id"],
                         "generation": "g", "ok": False, "code": "skill_timeout"})
            with pytest.raises(NeedsReconcile, match="skill_timeout"):
                pending.result(3)
            first.send({"protocol": VERSION, "kind": "skill_late_reply", "id": request["id"],
                        "generation": "g", "ok": True})
            time.sleep(.1)
            assert broker.router_snapshot(bound())["unknown"] == request["id"]
        finally:
            first.close()
            second.close()


def test_instance_bridge_disconnect_does_not_reset_unknown_on_same_generation_reconnect(tmp_path):
    from sico.transport.bridge_client import BridgeClient
    from sico.transport.instance_bridge import InstanceBridge

    with InstanceBridge(tmp_path, bound()) as bridge, ThreadPoolExecutor(1) as pool:
        first = connect_peer(bridge.broker, bound())
        with BridgeClient.discover(tmp_path, bound()) as client:
            client.register_session("desktop", bound())
            pending = pool.submit(client.get_context, bound())
            request = first.receive()
            first.close()
            with pytest.raises(NeedsReconcile):
                pending.result(3)
            second = connect_peer(bridge.broker, bound())
            try:
                assert client.router_snapshot(bound())["unknown"] == request["id"]
                with pytest.raises(NeedsReconcile, match="skill_blocked_unknown"):
                    client.get_context(bound())
            finally:
                second.close()


@pytest.mark.parametrize('disconnect_first', [False, True])
def test_same_generation_transport_and_registration_preserve_logical_session_counts(disconnect_first):
    import io
    diagnostic = io.StringIO()
    with ContextBroker(diagnostic=diagnostic) as broker:
        # InstanceBridge creates its generation router before the first hello.
        broker.expected_identity = (bound().instance_id, bound().generation)
        broker.registry.register(bound().instance_id, bound().generation)
        first = connect_peer(broker, bound())
        try:
            initial = broker.register_session('desktop', bound())
            record = broker._sessions['desktop']
            if disconnect_first:
                first.close()
                broker._discard_peer(broker.peers[bound().instance_id])
            second = connect_peer(broker, bound())
            try:
                repeated = broker.register_session('desktop', bound())
                assert broker._sessions['desktop'] is record
                assert repeated == initial
                events = [json.loads(line)['event'] for line in diagnostic.getvalue().splitlines()]
                assert events.count('bridge.instance_registered') == 1
                assert events.count('bridge.instance_transport_updated') == 1
                assert events.count('bridge.session_registered') == 1
                assert events.count('bridge.session_released') == 0
            finally:
                second.close()
        finally:
            first.close()


@pytest.mark.parametrize('outer_error', [False, True])
def test_transient_context_reply_does_not_retire_bridge_or_target(tmp_path, outer_error):
    from sico.transport.methods import QueryUnavailable

    context = bound()
    with ContextBroker() as broker, ThreadPoolExecutor(1) as pool:
        peer = connect_peer(broker, context)
        try:
            for _ in range(2):
                future = pool.submit(broker.get_context, context)
                request = peer.receive()
                peer.send({'protocol': VERSION, 'kind': 'response', 'id': request['id'],
                    'generation': context.generation, 'ok': not outer_error,
                    'code': 'context_unavailable',
                    'result': {'valid': False, 'code': 'context_unavailable'}})
                with pytest.raises(QueryUnavailable) as exc:
                    future.result(3)
                assert exc.value.code == 'context_unavailable'
            future = pool.submit(broker.get_context, context)
            request = peer.receive()
            reply(peer, request, 'recovered')
            assert future.result(3)['valid']
        finally:
            peer.close()
