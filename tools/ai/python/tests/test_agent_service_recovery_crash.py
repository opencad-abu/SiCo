"""Real SIGKILL, original acceptance evidence and paused recovery across service generations."""

import multiprocessing
import socket
import time
from contextlib import ExitStack
from types import SimpleNamespace

import pytest
from agent_service_business_fixtures import exchange, query_request
from agent_service_gui_fixtures import drain
from agent_service_lifecycle_fixtures import service_process

from sico.service.remote_frontend import RemoteFrontend
from sico.service.service_discovery import discover_project
from sico.service.service_messages import ServiceRequest
from sico.transport.framing import Connection


def crash_service(project, channel, entered, release, phase):
    from sico.service.inbox import SessionInbox
    from sico.service.service_entry import run_service
    from sico.storage.journal import SessionJournal

    save, append = SessionInbox._save, SessionJournal._append
    def pause():
        entered.set()
        assert release.wait(30)
    def saved(self, row):
        if phase == "before" and row["status"] == "queued":
            pause()
        save(self, row)
        if phase == "after" and row["status"] == "queued":
            pause()
    def appended(self, kind, payload, state):
        event = append(self, kind, payload, state)
        if kind == {"tool": "tool.started", "task": "task.started"}.get(phase):
            pause()
        return event
    SessionInbox._save, SessionJournal._append = saved, appended
    raise SystemExit(run_service(project, bootstrap_fd=channel.detach()))


@pytest.mark.parametrize("phase", ["before", "after", "task", "tool"])
def test_killed_service_recovers_without_inheriting_control_or_replaying_unknown_work(
        tmp_path, phase):
    context = multiprocessing.get_context("spawn")
    entered, release = context.Event(), context.Event()
    parent, child = socket.socketpair()
    process = context.Process(target=crash_service,
        args=(str(tmp_path), child, entered, release, phase))
    process.start()
    child.close()
    old = None
    try:
        assert Connection(parent).receive(deadline=time.monotonic() + 10)["kind"] == "ready"
        descriptor = discover_project(tmp_path).service
        old = RemoteFrontend(descriptor, tmp_path, "old")
        frontend = old.open_initial("demo", dict(session_id="old", provider_config=None,
            environment={}, timeout=3, context_file=None)).result(5)
        token = frontend.session
        old.control.request(token, "acquire").result(5)
        address = old.address(token)
        old._commands.request("submit", dict(address=address.record(), args=["inspect design"],
            kwargs={}), operation_id="original", control=old.control.proof(token))
        assert entered.wait(8), phase
        process.kill()
        process.join(5)
        assert not process.is_alive()
        old.close_desktop()
        with service_process(tmp_path) as (_new_process, new_descriptor), ExitStack() as stack:
            api = RemoteFrontend(new_descriptor, tmp_path, "old")
            stack.callback(api.close_desktop)
            assert new_descriptor.service_id != descriptor.service_id
            original = exchange(SimpleNamespace(descriptor=new_descriptor), query_request(address))
            # The submitted operation is 'original', not the default fixture ID.
            original = api.recovery.input_result(address.record(), "original").result(5).status
            assert original.outcome == ("unknown" if phase == "before" else "accepted")
            assert api._admin.request(ServiceRequest()).result(5).status.pending.sessions == 0
            view = api.recovery.inspect("old").result(5)
            assert view["state"] == "ready"
            result = api.recovery.restore(view, "recover-original").result(8)
            assert result["session"]["address"]["runtime_id"] != token.runtime_id
            new = api.open_session("old").result(5)
            assert not api.can_control(new)
            replay = api.prepare_replay(new, 1, 200).result(5)
            rows = [event for batch in drain(api, replay.stream) for event in batch.events]
            starts = [event for event in rows if event["kind"] == "task.started"]
            assert len(starts) == int(phase in {"task", "tool"})
            if phase in {"task", "tool"}:
                assert result["task_status"] == "needs_reconcile"
                assert any(event["kind"] == "task.needs_reconcile" for event in rows)
            assert api.recovery.inspect("old").result(5)["inputs"] == result["inputs"]
            assert api.recovery.inspect("old", "recover-original").result(5)["operation"]
    finally:
        parent.close()
        if old:
            old.close_desktop()
        # Do not signal a multiprocessing Event whose waiting process was killed.
        if process.is_alive():
            process.kill()
        process.join(5)
