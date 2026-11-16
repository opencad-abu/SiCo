"""One captured host loss pauses its session without ending project siblings."""

import threading
from contextlib import ExitStack

import pytest
from agent_service_business_fixtures import Provider, business_service, exchange, open_request
from agent_service_launch_fixtures import eventually
from test_agent_service_control import attach
from test_agent_service_host import host_bridge, target
from test_agent_service_shutdown import status

from sico.core.contracts import ModelTurn, TextDelta
from sico.transport.bridge_client import BridgeClient


class TwoSessions(Provider):
    def stream(self, system, messages, tools, cancelled):
        text = messages[-1]["content"]
        self.seen.append(text)
        if text == "host one active":
            self.entered.set()
            assert self.release.wait(8)
        yield TextDelta("done")
        yield ModelTurn("done")


def test_host_loss_pauses_queue_without_rebinding_or_affecting_sibling(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    provider = TwoSessions(entered, release)
    one = target(tmp_path)
    two = target(tmp_path, instance="other", cell="Other")
    with host_bridge(tmp_path, one) as (bridge, client), host_bridge(tmp_path, two) as (_, other), (
            business_service(tmp_path, monkeypatch, provider=provider,
                             bridge_factory=BridgeClient)) as service, ExitStack() as stack:
        exchange(service, open_request("live", context=one, bridge=client.descriptor))
        exchange(service, open_request("other", context=two, bridge=other.descriptor))
        api, token = attach(stack, service, tmp_path)
        sibling, other_token = attach(stack, service, tmp_path, "other")
        api.control.request(token, "acquire").result(5)
        sibling.control.request(other_token, "acquire").result(5)
        api.command(token, "submit", "host one active").result(5)
        assert entered.wait(3)
        queued = api.command(token, "submit", "queued on lost host").result(5)
        controller = service.owner.controllers["live"]
        try:
            bridge.close()
            eventually(lambda: controller.loop.stale and controller.paused)

            def reported():
                return [row["payload"] for row in controller.journal.events()
                        if row["kind"] == "session.target_unavailable"]

            eventually(lambda: bool(reported()))
            assert reported()[0]["source"] == "binding_sync_error"
            assert reported()[0]["upstream_reason"]
            assert controller.current == one and not controller.closing
            assert status(service).pending.sessions >= 2
            sibling.command(other_token, "submit", "healthy sibling").result(5)
            assert service.owner.controllers["other"].wait(5)
            release.set()
            assert controller.wait(5)
            assert controller._task["status"] == "needs_reconcile"
            assert api.query_input(token, queued).result(5).status.execution == "queued"
            assert provider.seen == ["host one active", "healthy sibling"]
            with pytest.raises(ValueError):
                api.command(token, "submit", "do not move target").result(5)
        finally:
            release.set()


def test_invalidated_queued_target_never_starts_provider(tmp_path, monkeypatch):
    bound = target(tmp_path)
    with host_bridge(tmp_path, bound) as (bridge, client), business_service(
            tmp_path, monkeypatch, bridge_factory=BridgeClient) as service, ExitStack() as stack:
        exchange(service, open_request(context=bound, bridge=client.descriptor))
        api, token = attach(stack, service, tmp_path)
        api.control.request(token, "acquire").result(5)
        controller = service.owner.controllers["live"]
        controller.paused = True
        key = api.command(token, "submit", "keep queued").result(5)
        with bridge.broker._changed:
            bridge.broker.binding_events.invalidate_target(bound, "window_closed")
        eventually(lambda: bound.target_id in (controller.bindings.events.state or {}).get(
            "invalidated", {}))
        api.command(token, "resume", expected_task="").result(5)
        assert controller.wait(5)
        assert not service.provider.seen and controller.paused
        assert api.query_input(token, key).result(5).status.execution == "queued"


def test_target_invalidation_journal_names_upstream_reason(tmp_path, monkeypatch):
    bound = target(tmp_path)
    with host_bridge(tmp_path, bound) as (bridge, client), business_service(
            tmp_path, monkeypatch, bridge_factory=BridgeClient) as service, ExitStack() as stack:
        exchange(service, open_request(context=bound, bridge=client.descriptor))
        api, token = attach(stack, service, tmp_path)
        api.control.request(token, "acquire").result(5)
        controller = service.owner.controllers["live"]
        with bridge.broker._changed:
            bridge.broker.binding_events.invalidate_target(bound, "window_closed")

        def reported():
            return [row["payload"] for row in controller.journal.events()
                    if row["kind"] == "session.target_unavailable"
                    and row["payload"].get("upstream_reason") == "window_closed"]

        eventually(lambda: bool(reported()))
        assert reported()[0]["reason"] == "captured_host_unavailable"
        assert reported()[0]["source"] == "target_invalidated"
        assert controller.paused
