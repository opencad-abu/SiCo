"""Circuit failure evidence crosses the canonical bridge client and instance owners."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from test_agent_circuit_errors import failure
from test_agent_project import bound

from sico.core.contracts import BoundContext, CircuitCallError, NeedsReconcile
from sico.transport.broker import skill_call_context


@pytest.mark.parametrize("fatal", [False, True])
def test_error_evidence_survives_bridge_rpc(tmp_path, fatal):
    from test_agent_bridge import connect_peer
    from test_agent_bridge import reply as success

    from sico.transport.bridge_client import BridgeClient
    from sico.transport.instance_bridge import InstanceBridge

    original_context = bound(tmp_path)
    context = BoundContext(
        original_context.instance_id,
        original_context.generation,
        original_context.target_id,
        {**original_context.snapshot, "capabilities": ["circuit_call"]},
    )
    with InstanceBridge(tmp_path, context) as bridge:
        peer = connect_peer(bridge.broker, context)
        try:
            with BridgeClient.discover(tmp_path, context) as client, ThreadPoolExecutor(1) as pool:
                client.register_session("session", context)

                def call():
                    with skill_call_context(session_id="session"):
                        return client.circuit_call(
                            context,
                            {
                                "function": "aiCopilotDescribe",
                                "values": ["run_simulation_setup", "setup", None, None],
                                "claim": None,
                                "expected": None,
                            },
                        )

                pending = pool.submit(call)
                request = peer.receive()
                response = failure("circuit_context_stale", "binding") if fatal else failure()
                response["id"] = request["id"]
                peer.send(response)
                with pytest.raises(NeedsReconcile if fatal else CircuitCallError) as caught:
                    pending.result(5)
                assert caught.value.data["raw_error"] == response["diagnostic"]["raw_error"]
                assert caught.value.data["output"] == response["diagnostic"]["output"]
                assert caught.value.data["request_id"] == request["id"]
                assert not caught.value.data["automatic_resume_allowed"]
                if not fatal:
                    pending = pool.submit(call)
                    success(peer, peer.receive(), "next read")
                    assert pending.result(5)["valid"]
        finally:
            peer.close()


def test_circuit_success_result_does_not_require_context_valid_flag(tmp_path):
    from test_agent_bridge import connect_peer

    from sico.transport.bridge_client import BridgeClient
    from sico.transport.framing import VERSION
    from sico.transport.instance_bridge import InstanceBridge

    original_context = bound(tmp_path)
    context = BoundContext(
        original_context.instance_id,
        original_context.generation,
        original_context.target_id,
        {**original_context.snapshot, "capabilities": ["circuit_call"]},
    )
    result = {
        "ok": True,
        "occupied_bbox": [[0, -0.1875], [0.25, 0.1875]],
        "pin_centers": [["G", [[0, 0]]]],
    }
    with InstanceBridge(tmp_path, context) as bridge:
        peer = connect_peer(bridge.broker, context)
        try:
            with BridgeClient.discover(tmp_path, context) as client, ThreadPoolExecutor(1) as pool:
                client.register_session("session", context)

                def call():
                    with skill_call_context(session_id="session"):
                        return client.circuit_call(
                            context,
                            {
                                "function": "aiCopilotDescribe",
                                "values": ["binding_geometry", "geometry", None, None],
                                "claim": None,
                                "expected": None,
                            },
                        )

                pending = pool.submit(call)
                request = peer.receive()
                peer.send({
                    "protocol": VERSION,
                    "kind": "response",
                    "id": request["id"],
                    "generation": request["generation"],
                    "ok": True,
                    "result": result,
                })
                assert pending.result(5) == result
        finally:
            peer.close()
