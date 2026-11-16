"""SIGKILL around input persistence, followed by read-only receipt reconciliation."""

import json
import multiprocessing
import os
import socket
import sys
import time
from types import SimpleNamespace

import pytest
from agent_service_business_fixtures import (
    Bridge,
    exchange,
    open_request,
    query_request,
    submit_request,
)
from agent_service_control_fixtures import ControlledInputClient
from agent_service_lifecycle_fixtures import service_process

from sico.service.service_discovery import discover_project
from sico.service.service_messages import ServiceRequest
from sico.transport.framing import Connection


def paused_input_service(project, channel, entered, release, after):
    import sico.service.service_entry as entry
    from sico.service.inbox import SessionInbox
    from sico.service.project_sessions import ProjectSessionOwner
    from sico.service.service_entry import run_service

    original = SessionInbox._save

    def save(self, row):
        if after:
            original(self, row)
        entered.set()
        assert release.wait(20)
        if not after:
            original(self, row)

    SessionInbox._save = save
    entry.ProjectSessionOwner = lambda path: ProjectSessionOwner(path, bridge_factory=Bridge)
    raise SystemExit(run_service(project, bootstrap_fd=channel.detach()))


@pytest.mark.parametrize("after", [False, True])
def test_crash_reconciliation_never_reexecutes_accepted_input(tmp_path, after):
    context = multiprocessing.get_context("spawn")
    entered, release = context.Event(), context.Event()
    parent, child = socket.socketpair()
    process = context.Process(target=paused_input_service,
        args=(str(tmp_path), child, entered, release, after))
    process.start()
    child.close()
    client = None
    try:
        assert Connection(parent).receive(deadline=time.monotonic() + 8)["kind"] == "ready"
        descriptor = discover_project(tmp_path).service
        service = SimpleNamespace(descriptor=descriptor)
        opened = exchange(service, open_request(environment={"SICO_PYTHON": sys.executable}))
        assert opened.outcome == "opened", opened
        address = opened.address
        client = ControlledInputClient(descriptor)
        completion = client.request(submit_request(address))
        assert entered.wait(5)
        assert not completion.done()
        assert exchange(service, ServiceRequest()).pending.sessions == 1
        process.kill()
        process.join(3)
        assert not process.is_alive()
        client.close()
        with service_process(
            tmp_path,
            environment=dict(os.environ, SICO_PYTHON=sys.executable),
        ) as (_replacement, new_descriptor):
            restarted = SimpleNamespace(descriptor=new_descriptor)
            result = exchange(restarted, query_request(address))
            assert result.outcome == ("accepted" if after else "unknown")
            assert result.recovery_required == after
            assert result.execution == ("queued" if after else None)
            assert exchange(restarted, ServiceRequest()).pending.sessions == 0
            retry = exchange(restarted, submit_request(address))
            assert retry.outcome == ("accepted" if after else "rejected")
            assert exchange(restarted, open_request()).code == "history_readonly"
            events = tmp_path / ".sico/ai/agent/sessions/live/events.jsonl"
            assert all(json.loads(line)["kind"] == "session.control"
                       for line in events.read_text().splitlines())
    finally:
        # SIGKILL may strand multiprocessing.Condition's waiter semaphore.
        # Never signal a shared Event after killing its waiting process.
        parent.close()
        if client is not None:
            client.close()
        if process.is_alive():
            process.kill()
        process.join(5)
