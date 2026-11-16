"""Real wire and controller fixtures, with controllable backend dependencies."""

import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace

from sico.core.contracts import BoundContext, ModelTurn, TextDelta
from sico.service.project_service import ProjectServiceLease
from sico.service.project_sessions import ProjectSessionOwner
from sico.service.service_allocation import Allocation
from sico.service.service_dispatch import ServiceDispatch
from sico.service.service_lifecycle import ServiceLifecycle
from sico.service.service_listener import serve_ready
from sico.service.service_management import exchange_service
from sico.service.service_messages import ServiceRequest
from sico.service.service_runtime import listening_socket

CONTEXT = BoundContext("instance", "generation", "target", {"valid": True})


class Bridge:
    def __init__(self, descriptor):
        self.closed = False

    def register_session(self, session_id, context):
        pass

    def register_target(self, context):
        pass

    def close(self):
        self.closed = True


class Provider:
    label = "controlled"
    base = model = "test"

    def __init__(self, entered=None, release=None):
        self.entered, self.release = entered, release
        self.seen = []

    def stream(self, system, messages, tools, cancelled):
        self.seen.append(messages[-1]["content"])
        if self.entered is not None:
            self.entered.set()
            assert self.release.wait(10)
        yield TextDelta("done")
        yield ModelTurn("done")


@contextmanager
def business_service(project, monkeypatch, *, provider=None, bridge_factory=Bridge, capacity=64):
    provider = provider or Provider()
    monkeypatch.setattr("sico.service.project_sessions.provider_from_config",
                        lambda *_args, **_kwargs: provider)
    with ProjectServiceLease.acquire(project) as lease, listening_socket() as (listener, endpoint):
        descriptor = lease.publish(endpoint)
        owner = ProjectSessionOwner(project, environment={}, bridge_factory=bridge_factory)
        with ServiceDispatch(owner, descriptor, capacity=capacity) as dispatch:
            stopped, failures = threading.Event(), []
            lifecycle = ServiceLifecycle(pending=dispatch.pending_work)

            def run():
                try:
                    serve_ready(listener, descriptor, stopped, lifecycle, Allocation(), dispatch)
                except BaseException as exc:
                    failures.append(exc)

            thread = threading.Thread(target=run, name="test-business-listener")
            thread.start()
            try:
                yield SimpleNamespace(descriptor=descriptor, owner=owner, dispatch=dispatch,
                                      provider=provider, thread=thread)
            finally:
                if provider.release is not None:
                    provider.release.set()
                dispatch.close()
                assert dispatch.wait(10)
                stopped.set()
                thread.join(3)
                assert not thread.is_alive() and not failures


def exchange(service, request):
    if request.method == "session.submit":
        from agent_service_control_fixtures import controlled_exchange

        return controlled_exchange(service.descriptor, request, time.monotonic() + 5).status
    return exchange_service(service.descriptor, request, deadline=time.monotonic() + 5).status


def open_request(session_id="live", *, context=CONTEXT, bridge=None, config=None, environment=None):
    return ServiceRequest("session.open", params=dict(
        session_id=session_id, context=context.record(), bridge=bridge or {},
        provider_config=config, environment=environment))


def open_session(service, session_id="live"):
    result = exchange(service, open_request(session_id))
    assert result.outcome == "opened"
    return result.address


def submit_request(address, operation_id="input", text="task", context=CONTEXT):
    return ServiceRequest("session.submit", operation_id, dict(
        address=address.record(), context=context.record(), text=text))


def query_request(address, operation_id="input"):
    return ServiceRequest("session.input", operation_id, dict(address=address.record()))
