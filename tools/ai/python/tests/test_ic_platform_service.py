"""Real service and instance sockets verify independent startup and selected identity."""

import json
import socket
import os
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest
from agent_service_business_fixtures import business_service
from sico.core.contracts import BoundContext
from sico.demo import DemoPeer
from sico.service.home_startup import HomeStartup
from sico.service.remote_frontend import RemoteFrontend
from sico.service.service_handshake import hello_message
from sico.service.service_lifecycle import ServiceLifecycle
from sico.service.service_protocol import heartbeat_message
from sico.service.service_wire import ServiceWire
from sico.transport.bridge_identity import discovery_path
from sico.transport.host_discovery import connect_candidate, list_instances
from sico.transport.instance_bridge import InstanceBridge


@contextmanager
def host(project, instance="v123", generation="g123"):
    context = BoundContext(instance, generation, "bound", dict(
        valid=True, source="simulated", cwd=str(project), version="IC23.1", entry="platform"))
    with InstanceBridge(project, context) as bridge:
        peer = DemoPeer(bridge.broker, context)
        try:
            bridge.broker.context(instance, timeout=3)
            yield bridge, context
        finally:
            peer.close()


def test_no_model_or_host_required_by_project_home(tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch) as service:
        monkeypatch.setattr("sico.service.frontend_startup.resolve_provider_config",
                            lambda *_a, **_k: pytest.fail("Home must not prepare a model"))
        args = SimpleNamespace(launch_dir=str(tmp_path), provider_config="missing.json")
        startup = HomeStartup(args, lambda *_a, **_k: None,
                              environment={"SICO_MODEL": "incomplete"})
        try:
            api = startup.ready.result(8)
            assert api.platforms.instances().result(5) == []
            assert not service.owner.controllers
            assert not service.provider.seen
            assert startup.configuration.result() is None
        finally:
            startup.close()
            assert startup.wait(5)


def test_public_candidates_redact_endpoints_and_reject_retired_or_foreign_identity(tmp_path):
    with host(tmp_path) as (bridge, context):
        rows = list_instances(tmp_path)
        assert rows == [dict(id=bridge.path.stem, platform="CDNS-IC", instance="v123",
                            generation="g123", version="IC23.1", project=str(tmp_path),
                            node=socket.gethostname(), job=os.environ.get("LSB_JOBID", ""))]
        assert bridge.descriptor["token"] not in json.dumps(rows)
        client, observed = connect_candidate(tmp_path, rows[0]["id"])
        with client:
            assert observed == context
        wrong = discovery_path(tmp_path, replace(context, generation="other"))
        wrong.write_text(bridge.path.read_text())
        wrong.chmod(0o600)
        assert len(list_instances(tmp_path)) == 1
        with pytest.raises(ValueError):
            connect_candidate(tmp_path, wrong.stem)
        with pytest.raises(ValueError):
            connect_candidate(tmp_path, "../../foreign")
        candidate = rows[0]["id"]
    assert not list_instances(tmp_path)
    with pytest.raises(ValueError):
        connect_candidate(tmp_path, candidate)


def test_same_machine_other_project_is_rejected(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    with host(other) as (bridge, context):
        from sico.storage.roots import agent_root

        agent_root(tmp_path, create=True)
        copied = discovery_path(tmp_path, context)
        copied.parent.mkdir(parents=True, mode=0o700)
        copied.write_bytes(bridge.path.read_bytes())
        copied.chmod(0o600)
        assert list_instances(tmp_path) == []
        with pytest.raises(ValueError):
            connect_candidate(tmp_path, copied.stem)


def test_stale_descriptors_do_not_hide_an_older_live_instance(tmp_path):
    with host(tmp_path) as (bridge, context):
        for index in range(40):
            retired = replace(context, generation="retired" + str(index))
            path = discovery_path(tmp_path, retired)
            path.write_text(json.dumps(dict(bridge.descriptor,
                generation=retired.generation, port=0)))
            path.chmod(0o600)
        assert [row["instance"] for row in list_instances(tmp_path)] == [context.instance_id]


def test_discovery_deadline_includes_a_stalled_handshake(tmp_path):
    import socket
    import time

    from sico.transport.host_discovery import connect_descriptor

    with host(tmp_path) as (bridge, _context), socket.socket() as stalled:
        stalled.bind(("127.0.0.1", 0))
        stalled.listen(1)
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            connect_descriptor(tmp_path, dict(bridge.descriptor, port=stalled.getsockname()[1]),
                               deadline=started + 0.15)
        assert time.monotonic() - started < 1


def test_selected_instance_and_unsupported_platforms(tmp_path, monkeypatch):
    from sico.transport.bridge_client import BridgeClient

    with business_service(tmp_path, monkeypatch, bridge_factory=BridgeClient) as service:
        with host(tmp_path, "v1", "g1"), host(tmp_path, "v2", "g2"):
            api = RemoteFrontend(service.descriptor, tmp_path, "")
            try:
                rows = api.platforms.instances().result(5)
                assert {row["instance"] for row in rows} == {"v1", "v2"}
                selected = next(row for row in rows if row["instance"] == "v2")
                frontend = api.platforms.open(selected["id"], None, {}).result(8)
                assert api.view(frontend.session).context.instance_id == "v2"
                assert len(service.owner.controllers) == 1
                assert not service.provider.seen
                for platform in ("EMPY-AE", "SNPS-CC"):
                    with pytest.raises(ValueError, match="暂未支持"):
                        api.platforms.instances(platform).result(5)
            finally:
                api.close_desktop()


def test_authenticated_attach_heartbeat_keeps_empty_home_service_alive(tmp_path, monkeypatch):
    from sico.service.service_handshake import accept_ready

    with business_service(tmp_path, monkeypatch) as service:
        now = [0.0]
        lifecycle = ServiceLifecycle(idle_seconds=2, clock=lambda: now[0])
        wire = ServiceWire()
        hello = hello_message(service.descriptor, purpose="attach")
        identity = accept_ready(wire.reply(hello, service.descriptor, lifecycle, None), hello)
        now[0] = 1.5
        wire.reply(heartbeat_message(identity), service.descriptor, lifecycle, None)
        now[0] = 3.0
        lifecycle.tick()
        assert not lifecycle.stopping
        now[0] = 4.0
        lifecycle.tick()
        assert lifecycle.stopping


def test_history_from_empty_home_can_continue_without_an_invalid_source_id(tmp_path, monkeypatch):
    from agent_service_recovery_fixtures import seed

    seed(tmp_path, monkeypatch)
    with business_service(tmp_path, monkeypatch) as service:
        api = RemoteFrontend(service.descriptor, tmp_path, "")
        try:
            frontend = api.recovery.open_history("old").result(5)
            assert frontend.session.session_id == "old"
            assert api.initial_id == "old"
            assert not service.owner.controllers
            token = api.recovery.continue_session("old").result(8)
            assert token.session_id == "old"
            assert not service.provider.seen
        finally:
            api.close_desktop()


def test_selected_host_environment_overrides_desktop_eda_markers(tmp_path, monkeypatch):
    from sico.transport.bridge_client import BridgeClient

    monkeypatch.setenv("SICO_VIRTUOSO_PATH", "/selected/IC/bin")
    monkeypatch.setenv("SICO_VIRTUOSO_LD_LIBRARY_PATH", "/selected/IC/lib")
    monkeypatch.delenv("CDS_LIC_FILE", raising=False)
    with business_service(tmp_path, monkeypatch, bridge_factory=BridgeClient) as service:
        with host(tmp_path) as (bridge, _context):
            api = RemoteFrontend(service.descriptor, tmp_path, "")
            try:
                frontend = api.platforms.open(bridge.path.stem, None,
                    {"SICO_VIRTUOSO_PATH": "/wrong/bin", "SICO_API_KEY": "test-only",
                     "CDS_LIC_FILE": "wrong-license"}).result(8)
                captured = service.owner.dependencies_for(frontend.session.session_id).environment
                assert captured["SICO_VIRTUOSO_PATH"] == "/selected/IC/bin"
                assert captured["SICO_VIRTUOSO_LD_LIBRARY_PATH"] == "/selected/IC/lib"
                assert captured["SICO_API_KEY"] == "test-only"
                assert "CDS_LIC_FILE" not in captured
            finally:
                api.close_desktop()
