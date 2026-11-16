"""Real service termination under blocked dependencies, retaining committed facts."""

import multiprocessing
import socket
import time
from pathlib import Path

import pytest

from sico.service.project_service import ProjectBusy, ProjectServiceLease
from sico.service.remote_frontend import RemoteFrontend
from sico.service.service_discovery import discover_project
from sico.transport.framing import Connection


def blocked_service(project, channel, entered, layer):
    import threading

    from sico.service import service_entry, service_exit
    from sico.service.frontend_hub import FrontendHub
    from sico.service.project_sessions import ProjectSessionOwner
    from sico.service.session_recovery import SessionRecovery
    from sico.storage.journal import SessionJournal

    def forever(*_args, **_kwargs):
        entered.set()
        threading.Event().wait()
    original_exit = service_exit.ServiceExit
    service_entry.ServiceExit = lambda stop: original_exit(stop, seconds=.8)
    if layer == "scan":
        SessionRecovery._scan = forever
    elif layer == "owner":
        ProjectSessionOwner._release = forever
    elif layer == "query":
        original = FrontendHub.execute
        def execute(hub, wire, row):
            if row["operation"] == "catalog":
                forever()
            return original(hub, wire, row)
        FrontendHub.execute = execute
    else:
        original = SessionJournal.append_session
        def append(journal, kind, payload, context):
            if kind == "session.end_observed":
                forever()
            return original(journal, kind, payload, context)
        SessionJournal.append_session = append
    raise SystemExit(service_entry.run_service(project, bootstrap_fd=channel.detach()))


@pytest.mark.parametrize("layer", ["scan", "owner", "query", "journal"])
def test_signal_exit_budget_keeps_project_lease_until_process_dies(tmp_path, layer):
    context = multiprocessing.get_context("spawn")
    entered = context.Event()
    parent, child = socket.socketpair()
    process = context.Process(target=blocked_service, args=(str(tmp_path), child, entered, layer))
    process.start()
    child.close()
    api = None
    try:
        assert Connection(parent).receive(deadline=time.monotonic() + 8)["kind"] == "ready"
        descriptor = discover_project(tmp_path).service
        api = RemoteFrontend(descriptor, tmp_path, "blocked")
        if layer == "query":
            api._queries.request("catalog", dict(version=-1))
        elif layer == "journal":
            frontend = api.open_initial("demo", dict(session_id="blocked", provider_config=None,
                environment={}, timeout=3, context_file=None)).result(5)
            api.control.request(frontend.session, "acquire").result(5)
            api.end_session(frontend.session, "blocked-end").result(5)
        if layer != "owner":
            assert entered.wait(3)
        start = time.monotonic()
        process.terminate()
        assert entered.wait(3)
        with pytest.raises(ProjectBusy):
            ProjectServiceLease.acquire(tmp_path)
        process.join(3)
        assert not process.is_alive() and process.exitcode == 3
        assert time.monotonic() - start < 2.5
        with ProjectServiceLease.acquire(tmp_path):
            pass
        assert not Path(descriptor.endpoint).exists()
        if layer == "journal":
            from sico.service.recovery_facts import read_facts

            facts = read_facts(tmp_path, "blocked")
            assert facts.unresolved and facts.ends[-1]["kind"] == "session.end_requested"
    finally:
        parent.close()
        if api is not None:
            api.close_desktop()
        if process.is_alive():
            process.kill()
        process.join(3)
