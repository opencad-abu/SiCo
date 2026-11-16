"""Production registration, demo and connect all attach through the project service."""

import threading
from concurrent.futures import CancelledError
from dataclasses import replace

import pytest
from agent_gui_wait import wait_until
from agent_service_business_fixtures import business_service
from agent_service_gui_fixtures import drain, startup_args
from agent_worker_fixtures import heartbeat
from PyQt5.QtWidgets import QLabel
from sico.core.contracts import BoundContext
from sico.demo import DemoPeer
from sico.service.frontend_startup import FrontendStartup
from sico.service.remote_frontend import RemoteFrontend
from sico.transport.bridge_client import BridgeClient
from test_agent_service_host import host_bridge, submit, target


@pytest.mark.parametrize("mode", ["desktop-worker", "demo", "connect"])
def test_all_entrypoints_use_service_owned_dependencies(tmp_path, monkeypatch, mode):
    peers = []

    def relay(_journal, broker, instance, generation, **kwargs):
        peer = DemoPeer(broker, target(tmp_path, "bound", instance=instance,
                                      generation=generation))
        peers.append(peer)
        return "Connect to the captured relay"

    monkeypatch.setattr("sico.service.frontend_attach.connection_instructions", relay)
    with host_bridge(tmp_path, target(tmp_path)) as (bridge, _client), business_service(
            tmp_path, monkeypatch, bridge_factory=BridgeClient) as service:
        startup = FrontendStartup(startup_args(tmp_path, mode), lambda *_a, **_k: None,
                                  environment={})
        try:
            if mode == "desktop-worker":
                startup.start(dict(bridge=bridge.descriptor, context=target(tmp_path).record()))
            ready = startup.ready.result(5)
            api, token = ready.frontend.api, ready.frontend.session
            assert isinstance(api, RemoteFrontend)
            controller = service.owner.controllers[token.session_id]
            assert api.view(token).context == controller.current
            assert service.owner.dependencies_for(token.session_id).environment == {}
            assert api.command(token, "submit", "captured input").result(5)
            assert controller.wait(5)
            replay = api.prepare_replay(token, 4, 200).result(5)
            batches = drain(api, replay.stream)
            assert any(e["kind"] == "task.completed" for b in batches for e in b.events)
            if mode in {"demo", "connect"}:
                context = api.view(token).context
                snapshot = dict(context.snapshot, cellview=dict(context.snapshot["cellview"],
                                                               cell="Sibling"))
                captured = replace(context, target_id="sibling-target", snapshot=snapshot)
                sibling = api.create_session(captured).result(5)
                assert sibling != token
                service.owner.close_session(token.session_id)
                assert api.command(sibling, "submit", "sibling survives").result(5)
                assert service.owner.controllers[sibling.session_id].wait(5)
                assert not service.owner._bundles[sibling.session_id].bridge._stop.is_set()
        finally:
            startup.close()
            assert startup.wait(5)
            for peer in peers:
                peer.close()
        assert not api.owns(token)
        assert service.owner.controllers


@pytest.mark.parametrize("phase", ["config", "provider", "connect"])
def test_slow_startup_dependency_and_cancellation_do_not_block_qt(
        tmp_path, monkeypatch, phase):
    entered, release = threading.Event(), threading.Event()
    widget = QLabel("startup")
    with business_service(tmp_path, monkeypatch) as service:
        module, name = {
            "config": ("sico.service.frontend_startup", "resolve_provider_config"),
            "provider": ("sico.service.project_sessions", "provider_from_config"),
            "connect": ("sico.service.frontend_attach", "connection_instructions"),
        }[phase]
        import importlib

        target_module = importlib.import_module(module)
        original = getattr(target_module, name)

        def paused(*args, **kwargs):
            entered.set()
            assert release.wait(5)
            return "No relay attached" if phase == "connect" else original(*args, **kwargs)

        monkeypatch.setattr(target_module, name, paused)
        startup = FrontendStartup(startup_args(tmp_path, "connect" if phase == "connect"
                                               else "demo"), lambda *_a, **_k: None,
                                  environment={})
        try:
            wait_until(entered.is_set)
            heartbeat(widget)
            startup.close()
            heartbeat(widget)
            assert not release.is_set()
            release.set()
            assert startup.wait(5)
            with pytest.raises(CancelledError):
                startup.ready.result()
            assert service.thread.is_alive()
        finally:
            release.set()
            startup.close()
            assert startup.wait(5)
            widget.close()


def test_production_reopen_observes_existing_host_session_without_reconfiguration(
        tmp_path, monkeypatch):
    context = target(tmp_path)
    with host_bridge(tmp_path, context) as (bridge, client), business_service(
            tmp_path, monkeypatch, bridge_factory=BridgeClient) as service:
        accepted = submit(service, client, context, environment={"CAPTURED": "original"})
        api = RemoteFrontend(service.descriptor, tmp_path, "new-window-request")
        try:
            frontend = api.open_initial("open", dict(session_id="new-window-request",
                bridge=bridge.descriptor, context=context, provider_config=None,
                environment={"CAPTURED": "new-gui"})).result(5)
            assert frontend.session == accepted.address.session
            assert len(service.owner.controllers) == 1
            assert service.owner.dependencies_for(frontend.session.session_id).environment == {
                "CAPTURED": "original"}
        finally:
            api.close_desktop()


def test_windowless_reopen_reuses_the_active_session_across_target_handles(
        tmp_path, monkeypatch):
    """Closing a window detaches it; reopening the same project continues its session."""
    context = BoundContext("instance", "generation", "first",
                           {"cwd": str(tmp_path), "valid": True, "source": "simulated"})
    with host_bridge(tmp_path, context) as (bridge, client), business_service(
            tmp_path, monkeypatch, bridge_factory=BridgeClient) as service:
        accepted = submit(service, client, context)
        first_api = RemoteFrontend(service.descriptor, tmp_path, "first-window")
        first = first_api.open_initial("open", dict(session_id="first-window",
            bridge=bridge.descriptor, context=context, provider_config=None,
            environment={})).result(5)
        first_api.control.request(first.session, "acquire").result(5)
        first_api.close_desktop()
        reopened = replace(context, target_id="second")
        client.register_target(reopened)
        startup = FrontendStartup(startup_args(tmp_path, "desktop-worker",
                                               session="new-window-request"),
                                  lambda *_a, **_k: None, environment={})
        try:
            startup.start(dict(bridge=bridge.descriptor, context=reopened.record()))
            ready = startup.ready.result(5)
            assert ready.frontend.session == accepted.address.session
            assert len(service.owner.controllers) == 1
            assert ready.frontend.api.owns(ready.frontend.session)
        finally:
            startup.close()
            assert startup.wait(5)


def test_windowless_reopen_does_not_merge_a_design_session(tmp_path, monkeypatch):
    context = BoundContext("instance", "generation", "first",
                           {"cwd": str(tmp_path), "valid": True, "source": "simulated"})
    design = target(tmp_path, "design")
    with host_bridge(tmp_path, context) as (bridge, client), business_service(
            tmp_path, monkeypatch, bridge_factory=BridgeClient) as service:
        accepted = submit(service, client, context)
        client.register_target(design)
        api = RemoteFrontend(service.descriptor, tmp_path, "design-window")
        try:
            frontend = api.open_initial("open", dict(session_id="design-window",
                bridge=bridge.descriptor, context=design, provider_config=None,
                environment={})).result(5)
            assert frontend.session.session_id != accepted.address.session.session_id
            assert len(service.owner.controllers) == 2
        finally:
            api.close_desktop()


def test_connect_uses_client_captured_context_environment(tmp_path, monkeypatch):
    from sico.service.connection import connection_instructions

    context = tmp_path / "client-runtime.cxt"
    context.write_bytes(b"context reference for connection text only")
    monkeypatch.setenv("SICO_AI_CONTEXT", str(tmp_path / "unrelated-service-runtime.cxt"))
    peers, instructions = [], []

    def relay(journal, broker, instance, generation, **kwargs):
        text = connection_instructions(journal, broker, instance, generation, **kwargs)
        instructions.append(text)
        peers.append(DemoPeer(broker, target(tmp_path, "bound", instance=instance,
                                            generation=generation)))
        return text

    monkeypatch.setattr("sico.service.frontend_attach.connection_instructions", relay)
    with business_service(tmp_path, monkeypatch):
        startup = FrontendStartup(startup_args(tmp_path, "connect"), lambda *_a, **_k: None,
                                  environment={"SICO_AI_CONTEXT": str(context)})
        try:
            startup.ready.result(5)
            assert str(context) in instructions[0]
            assert "unrelated-service-runtime" not in instructions[0]
        finally:
            startup.close()
            assert startup.wait(5)
            for peer in peers:
                peer.close()


@pytest.mark.parametrize('mode', ['desktop-worker', 'demo'])
def test_end_last_then_create_preserves_real_entry_dependencies(tmp_path, monkeypatch, mode):
    with host_bridge(tmp_path, target(tmp_path)) as (bridge, _client), business_service(
            tmp_path, monkeypatch, bridge_factory=BridgeClient) as service:
        startup = FrontendStartup(startup_args(tmp_path, mode), lambda *_a, **_k: None,
                                  environment={'CAPTURED': 'original'})
        try:
            if mode == 'desktop-worker':
                startup.start(dict(bridge=bridge.descriptor, context=target(tmp_path).record()))
            ready = startup.ready.result(5)
            api, token = ready.frontend.api, ready.frontend.session
            context = api.view(token).context
            for index in range(2):
                previous = token
                api.end_session(token, 'end-last-' + str(index)).result(5)
                wait_until(lambda: previous.session_id not in service.owner.controllers)
                token = api.create_session(context).result(5)
                assert api.view(token).context == context
                assert service.owner.dependencies_for(token.session_id).environment == {
                    'CAPTURED': 'original'}
                assert api.command(token, 'submit', 'after end ' + str(index)).result(5)
                assert service.owner.controllers[token.session_id].wait(5)
                assert previous.session_id not in service.owner.controllers
        finally:
            startup.close()
            assert startup.wait(5)
