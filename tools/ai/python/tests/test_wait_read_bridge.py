"""Real authenticated bridge and FIFO tests with an explicitly simulated peer."""

import socket
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_agent_bridge import bound

from sico.core.contracts import ToolResult
from sico.demo import DemoPeer
from sico.transport import input_gate
from sico.transport.bridge_client import BridgeClient
from sico.transport.broker import skill_call_context
from sico.transport.framing import Connection
from sico.transport.instance_bridge import InstanceBridge
from sico.transport.methods import QueryUnavailable


@pytest.mark.parametrize("invalidate", [False, True])
def test_bridge_rechecks_after_instance_queue_and_before_native_entry(tmp_path, invalidate):
    entered, release, queued = threading.Event(), threading.Event(), threading.Event()
    waiting, wire_calls, checks = [], [], []
    with InstanceBridge(tmp_path, bound()) as bridge, ThreadPoolExecutor(2) as pool:
        peer = DemoPeer(bridge.broker, bound())
        bridge.broker.context(bound().instance_id)
        native = bridge.broker.peers[bound().instance_id]
        original = native.call

        def spy(*args, **kwargs):
            wire_calls.append(args[3])
            return original(*args, **kwargs)

        native.call = spy

        def blocker(request_id):
            entered.set()
            assert release.wait(4)

        def gate():
            checks.append(bool(waiting))
            return ToolResult("waiting_user", "Missing input") if waiting else None

        try:
            with BridgeClient.discover(tmp_path, bound()) as client:
                client.register_session("owner", bound())
                first = pool.submit(bridge.router.execute, "controlled-blocker", blocker)
                assert entered.wait(2)

                def run():
                    with skill_call_context(session_id="owner", task_id="task", tool_call_id="tool",
                            before_start=gate, progress=lambda row:
                            queued.set() if row["state"] == "queued" else None):
                        return client.get_context(bound())

                future = pool.submit(run)
                assert queued.wait(2)
                if invalidate:
                    waiting.append(True)
                release.set()
                first.result(3)
                if invalidate:
                    with pytest.raises(QueryUnavailable) as raised:
                        future.result(3)
                    assert raised.value.code == "waiting_user"
                    assert wire_calls == [] and checks == [False, True]
                    waiting.clear()
                    assert wire_calls == []  # Answer/clear does not replay the queued operation.
                else:
                    assert future.result(3)["valid"] is True
                    assert len(wire_calls) == 1 and checks == [False, False]
                assert bridge.router.snapshot()["unknown"] is None
                assert bridge.router.snapshot()["queue"] == []
        finally:
            release.set()
            peer.close()


def test_older_bridge_cannot_silently_skip_dispatch_check(tmp_path):
    with InstanceBridge(tmp_path, bound()) as bridge:
        peer = DemoPeer(bridge.broker, bound())
        try:
            with BridgeClient.discover(tmp_path, bound()) as client:
                client.register_session("owner", bound())
                client.descriptor.pop("capabilities")
                with skill_call_context(session_id="owner", before_start=lambda: None):
                    with pytest.raises(QueryUnavailable) as raised:
                        client.get_context(bound())
                assert raised.value.code == "waiting_user"
                assert not bridge.router.snapshot()["active"]
        finally:
            peer.close()


@pytest.mark.parametrize("fault", ["nonce", "truthy", "integer", "timeout", "closed", "callback"])
def test_dispatch_check_is_one_use_bounded_and_fail_closed(monkeypatch, fault):
    monkeypatch.setattr(input_gate, "TIMEOUT", 0.05)
    left, right = socket.socketpair()
    server, client = Connection(left), Connection(right)
    closed = threading.Event()
    if fault == "closed":
        closed.set()
    try:
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(input_gate.remote_gate(server, closed))
            message = client.receive()
            if fault == "nonce":
                client.send({**message, "nonce": "f" * 32, "allowed": True})
            elif fault == "truthy":
                client.send({**message, "allowed": "true"})
            elif fault == "integer":
                client.send({**message, "allowed": 1})
            elif fault == "callback":
                def broken():
                    raise RuntimeError("private question contents")
                input_gate.answer_gate(client, message, broken)
            with pytest.raises(QueryUnavailable) as raised:
                future.result(2)
            assert raised.value.code == "waiting_user"
            assert "private question" not in str(raised.value)
    finally:
        client.close()
        server.close()
