"""Real service process helpers with deterministic shutdown and startup barriers."""

import multiprocessing
import os
import signal
import socket
import time

import pytest

from sico.service.background_watchdog import process_identity
from sico.service.service_discovery import discover_project
from sico.service.service_entry import run_service
from sico.service.service_startup import ServiceStartup


def eventually(check, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("Service did not reach expected state")


def stop_service(descriptor):
    try:
        current = process_identity(descriptor.pid)
    except (FileNotFoundError, ProcessLookupError):
        return
    assert current["start"] == descriptor.process_start
    os.kill(descriptor.pid, signal.SIGTERM)

    def exited():
        try:
            return process_identity(descriptor.pid)["state"] in {"Z", "X"}
        except (FileNotFoundError, ProcessLookupError):
            return True
    try:
        eventually(exited, 5)
    finally:
        if not exited():
            os.kill(descriptor.pid, signal.SIGKILL)


@pytest.fixture
def services():
    projects, waiters = [], []

    def start(project, **kwargs):
        projects.append(project)
        waiter = ServiceStartup(project, **kwargs)
        waiters.append(waiter)
        return waiter

    yield start
    for waiter in waiters:
        waiter.close()
        assert waiter.wait(5)
    for project in set(projects):
        found = discover_project(project)
        if found.service is not None:
            stop_service(found.service)


def launch_together(project, barrier, pipe):
    try:
        barrier.wait(10)
        startup = ServiceStartup(project, timeout=10)
        descriptor = startup.ready.result(12)
        assert startup.wait(2)
        pipe.send(descriptor)
    finally:
        pipe.close()


def _controlled_child(project, parent, channel, entered, release, boundary, failure):
    from sico.service import project_service, service_entry, service_runtime
    from sico.storage import project_files

    parent.close()

    def pause():
        entered.set()
        if not release.wait(10):
            raise TimeoutError("Test did not release service startup")
        if failure:
            raise OSError("injected service failure")

    if boundary in {"project.json", "discovery.json"}:
        original = project_files.os.fsync

        def fsync(fd):
            path = os.readlink("/proc/self/fd/" + str(fd))
            if "/" + boundary + "." in path and path.endswith(".tmp"):
                pause()
            return original(fd)
        project_files.os.fsync = fsync
    elif boundary == "runtime":
        original = service_runtime.validate_local_runtime

        def validate(path):
            pause()
            return original(path)
        service_runtime.validate_local_runtime = validate
    elif boundary == "publication":
        original = project_service.ProjectServiceLease.publish

        def publish(self, endpoint):
            pause()
            return original(self, endpoint)
        project_service.ProjectServiceLease.publish = publish
    else:
        original = service_entry.serve_ready

        def serve(*args):
            pause()
            return original(*args)
        service_entry.serve_ready = serve
    raise SystemExit(run_service(project, bootstrap_fd=channel.detach()))


def controlled_spawn(monkeypatch, *, boundary, failure=False):
    from sico.service import service_startup

    context = multiprocessing.get_context("fork")
    entered, release = context.Event(), context.Event()
    processes = []

    def spawn(project):
        parent, child = socket.socketpair()
        process = context.Process(target=_controlled_child,
            args=(project, parent, child, entered, release, boundary, failure))
        process.start()
        child.close()
        processes.append(process)
        return parent
    monkeypatch.setattr(service_startup, "spawn_service", spawn)
    return entered, release, processes


def reap_controlled(processes):
    for process in processes:
        if process.is_alive():
            process.terminate()
        process.join(5)
        if process.is_alive():
            process.kill()
            process.join(5)
            raise AssertionError("Controlled service outlived test")
