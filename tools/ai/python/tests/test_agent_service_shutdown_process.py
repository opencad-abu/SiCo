"""Real services preserve ended history and serialize idle exit with new attachment."""

import time
from contextlib import ExitStack

from agent_service_launch_fixtures import eventually
from agent_service_lifecycle_fixtures import service_process
from sico.service.remote_frontend import RemoteFrontend
from sico.service.service_lifecycle import ServiceLifecycle
from sico.service.service_management import exchange_service
from sico.service.service_messages import ServiceRequest


def open_demo(stack, descriptor, project):
    api = RemoteFrontend(descriptor, project, "live")
    stack.callback(api.close_desktop)
    token = api.open_initial("demo", dict(session_id="live", provider_config=None,
        environment={}, timeout=3, context_file=None)).result(5).session
    api.control.request(token, "acquire").result(5)
    return api, token


def test_last_session_end_allows_real_idle_exit_after_client_detach(tmp_path):
    with service_process(tmp_path, idle_seconds=1) as (process, descriptor), ExitStack() as stack:
        api, token = open_demo(stack, descriptor, tmp_path)
        operation = api.command(token, "submit", "retain ended history").result(5)
        eventually(lambda: api.query_input(token, operation).result(5).status.execution
                   in {"completed", "failed", "cancelled"})
        api.end_session(token, "last-end").result(5)
        eventually(lambda: api.end_session(token, "last-end", observe=True).result(5).state
                   == "ended")
        api.close_desktop()
        assert process.wait(timeout=6) == 0
        assert (tmp_path / ".sico/ai/agent/sessions/live/events.jsonl").is_file()


def test_explicit_stop_after_session_end_releases_real_service(tmp_path):
    with service_process(tmp_path) as (process, descriptor), ExitStack() as stack:
        api, token = open_demo(stack, descriptor, tmp_path)
        status = exchange_service(
            descriptor, ServiceRequest('service.status'), deadline=time.monotonic() + 5
        ).status
        assert status.pending.sessions >= 1
        api.end_session(token, "stop-end").result(5)
        eventually(lambda: api.end_session(token, "stop-end", observe=True).result(5).state
                   == "ended")
        reply = exchange_service(
            descriptor, ServiceRequest('service.stop'), deadline=time.monotonic() + 5
        ).status.record()
        assert reply["outcome"] == "stop_requested"
        api.close_desktop()
        assert process.wait(timeout=6) == 0


def test_stop_with_live_session_exits_real_service(tmp_path):
    with service_process(tmp_path) as (process, descriptor), ExitStack() as stack:
        api, token = open_demo(stack, descriptor, tmp_path)
        status = exchange_service(
            descriptor, ServiceRequest('service.status'), deadline=time.monotonic() + 5
        ).status
        assert status.pending.sessions >= 1
        reply = exchange_service(
            descriptor, ServiceRequest('service.stop'), deadline=time.monotonic() + 5
        ).status.record()
        assert reply["outcome"] == "stop_requested"
        api.close_desktop()
        assert process.wait(timeout=8) in (0, 3)


def test_ended_session_starts_fresh_idle_period_and_attach_wins_before_exit(tmp_path, monkeypatch):
    from agent_service_business_fixtures import business_service, open_session
    from test_agent_service_control import attach

    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service)
        api, token = attach(stack, service, tmp_path)
        api.control.request(token, "acquire").result(5)
        now = [0.0]
        lifecycle = ServiceLifecycle(idle_seconds=10, pending=service.dispatch.pending_work,
                                     clock=lambda: now[0])
        assert lifecycle.status()["idle_remaining"] is None
        api.end_session(token, "idle-end").result(5)
        eventually(lambda: api.end_session(token, "idle-end", observe=True).result(5).state
                   == "ended")
        now[0] = 100
        assert lifecycle.status()["idle_remaining"] == 10
        now[0] = 110
        assert lifecycle.attach()
        lifecycle.tick()
        assert not lifecycle.stopping
        now[0] = 120
        lifecycle.tick()
        assert lifecycle.stopping and not lifecycle.attach()
