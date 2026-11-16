"""Host payload and route records fail closed at the service boundary."""

import json

import pytest

from sico.service.host_contract import capture_payload
from sico.service.project_service import ProjectServiceLease
from sico.storage.host_routes import HostRoutes
from sico.transport.framing import ProtocolError


def test_host_payload_rejects_unbounded_environment_and_handles():
    with pytest.raises(ProtocolError):
        capture_payload({"message": {}, "provider_config": None,
                         "environment": {"X": "a" * (512 * 1024)}})


def test_route_record_rejects_tampered_identity(tmp_path):
    with ProjectServiceLease.acquire(tmp_path) as lease:
        routes = HostRoutes(tmp_path, lease.project_id)
        host = {"instance_id": "i" * 32, "generation": "g" * 32,
                "bridge_id": "b" * 32, "router_id": "r" * 32}
        path = routes.path(host, "input")
        path.parent.mkdir(parents=True, mode=0o700)
        path.write_text(json.dumps({"contract": "sico_host_route.v1", "project_id": "x"}))
        with pytest.raises(ProtocolError):
            routes.read(host, "input")
