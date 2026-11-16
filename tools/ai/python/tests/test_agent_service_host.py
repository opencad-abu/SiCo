"""Host inputs use the real instance bridge, service wire, inbox and binding routing."""

from contextlib import contextmanager

import pytest
from agent_service_business_fixtures import business_service, exchange

from sico.core.contracts import BoundContext
from sico.demo import DemoPeer
from sico.service.host_contract import host_identity
from sico.service.service_messages import ServiceRequest
from sico.transport.bridge_client import BridgeClient
from sico.transport.framing import ProtocolError
from sico.transport.instance_bridge import InstanceBridge


def target(root, key="initial", *, instance="instance", generation="generation", cell="Amp",
           view="schematic"):
    return BoundContext(instance, generation, key, dict(
        valid=True, source="simulated", cwd=str(root),
        cellview=dict(lib="Work", cell=cell, view=view)))


def payload(context, text="Ask", environment=None):
    return dict(message=dict(kind="submit", id=context.target_id, text=text,
                             context=context.record()),
                provider_config=None, environment=environment or {})


@contextmanager
def host_bridge(root, context):
    with InstanceBridge(root, context) as bridge:
        peer = DemoPeer(bridge.broker, context)
        try:
            with BridgeClient(bridge.descriptor) as client:
                yield bridge, client
        finally:
            peer.close()


def submit(service, client, context, **kwargs):
    client.register_target(context)
    reference = client.stage_host_payload(payload(context, **kwargs))
    return exchange(service, ServiceRequest("host.submit", context.target_id,
                                           dict(bridge=client.descriptor, **reference)))


def query(service, client, key):
    return exchange(service, ServiceRequest("host.input", key,
                                           dict(host=host_identity(client.descriptor))))


def test_routing_retry_and_restart_use_one_durable_destination(tmp_path, monkeypatch):
    initial = target(tmp_path)
    with host_bridge(tmp_path, initial) as (_bridge, client):
        with business_service(tmp_path, monkeypatch, bridge_factory=BridgeClient) as service:
            first_context = target(tmp_path, "first")
            first = submit(service, client, first_context)
            assert first.outcome == "accepted"
            controller = service.owner.controllers[first.address.session.session_id]
            assert controller.wait(3)
            events = controller.journal.events()
            controller.fault = "target later failed"
            again = submit(service, client, first_context)
            assert again.outcome == "accepted" and again.address == first.address
            assert controller.journal.events() == events
            assert (submit(service, client, first_context, text="changed").code
                    == "operation_conflict")
            controller.fault = ""
            related = submit(service, client, target(tmp_path, "related", view="layout"))
            assert related.address == first.address
            other = submit(service, client, target(tmp_path, "other", cell="Other"))
            assert other.address != first.address
            assert len(service.owner.controllers) == 2
        with business_service(tmp_path, monkeypatch, bridge_factory=BridgeClient) as restarted:
            found = query(restarted, client, "first")
            assert found.outcome == "accepted" and found.address == first.address
            assert submit(restarted, client, first_context).address == first.address
            assert not restarted.owner.controllers and not restarted.provider.seen


def test_instances_and_environments_are_isolated(tmp_path, monkeypatch):
    with host_bridge(tmp_path, target(tmp_path)) as (_, first), host_bridge(
            tmp_path, target(tmp_path, instance="other")) as (_, second), business_service(
                tmp_path, monkeypatch, bridge_factory=BridgeClient) as service:
        a = submit(service, first, target(tmp_path, "same"), environment={"PATH": "a" * 9000})
        b = submit(service, second, target(tmp_path, "same", instance="other"),
                   environment={"PATH": "b" * 9000})
        assert a.outcome == b.outcome == "accepted" and a.address != b.address
        assert query(service, first, "same").address == a.address
        assert query(service, second, "same").address == b.address
        assert (service.owner.dependencies_for(a.address.session.session_id).environment["PATH"]
                == "a" * 9000)
        assert (service.owner.dependencies_for(b.address.session.session_id).environment["PATH"]
                == "b" * 9000)


def test_payload_damage_keeps_protocol_error(tmp_path, monkeypatch):
    with host_bridge(tmp_path, target(tmp_path)) as (_, client), business_service(
            tmp_path, monkeypatch, bridge_factory=BridgeClient) as service:
        reference = client.stage_host_payload(payload(target(tmp_path, "input")))
        reference["digest"] = "f" * 64
        with pytest.raises(ProtocolError):
            exchange(service, ServiceRequest("host.submit", "input",
                                            dict(bridge=client.descriptor, **reference)))
        assert not service.owner.controllers
