"""Independent service with a process barrier and GUI-only ownership traps."""

import multiprocessing
import os
import socket
import time
from contextlib import contextmanager
from types import SimpleNamespace

from sico.service.service_discovery import discover_project
from sico.transport.framing import Connection


def _run_service(project, channel, entered, release, evidence):
    from sico.core.contracts import ModelTurn, TextDelta
    from sico.service import project_sessions
    from sico.service.service_entry import run_service

    class Provider:
        label = "controlled"
        base = model = "as07"

        def stream(self, system, messages, tools, cancelled):
            evidence.send((os.getpid(), messages[-1]["content"]))
            entered.set()
            if not release.wait(20):
                raise TimeoutError("Service test barrier expired")
            yield TextDelta("completed in service")
            yield ModelTurn("completed in service")

    project_sessions.provider_from_config = lambda *a, **kw: Provider()
    try:
        raise SystemExit(run_service(project, bootstrap_fd=channel.detach()))
    finally:
        evidence.close()


@contextmanager
def isolated_service(project):
    context = multiprocessing.get_context("spawn")
    entered, release = context.Event(), context.Event()
    evidence, writer = context.Pipe(duplex=False)
    parent, child = socket.socketpair()
    process = context.Process(target=_run_service,
        args=(project, child, entered, release, writer))
    process.start()
    child.close()
    writer.close()
    try:
        assert Connection(parent).receive(deadline=time.monotonic() + 10)["kind"] == "ready"
        descriptor = discover_project(project).service
        assert descriptor is not None and descriptor.pid == process.pid
        yield SimpleNamespace(process=process, descriptor=descriptor, entered=entered,
                              release=release, evidence=evidence)
    finally:
        release.set()
        parent.close()
        evidence.close()
        if process.is_alive():
            process.terminate()
        process.join(6)
        if process.is_alive():
            process.kill()
            process.join(5)
            raise AssertionError("Independent service outlived its fixture")


def forbid_gui_owners(monkeypatch):
    """Patch constructors in the GUI process, including backend worker threads."""
    import agent_sessions_harness as sessions

    from sico.providers import config
    from sico.service import (
        backend,
        controller,
        project_sessions,
        service_entry,
        session_recovery,
    )
    from sico.storage.journal import SessionJournal
    from sico.transport.broker import ContextBroker

    calls = []
    def forbidden(*args, **kwargs):
        calls.append(os.getpid())
        raise AssertionError("Execution owner constructed inside GUI process")

    targets = [(SessionJournal, "__init__"), (ContextBroker, "__init__"),
        (controller.SessionController, "__init__"), (sessions.DesktopSessions, "__init__"),
        (project_sessions.ProjectSessionOwner, "__init__"),
        (session_recovery.SessionRecovery, "__init__"),
        (config, "provider_from_config"), (project_sessions, "provider_from_config"),
        (backend, "create_backend"), (service_entry, "run_service")]
    for target, name in targets:
        monkeypatch.setattr(target, name, forbidden)
    return calls
