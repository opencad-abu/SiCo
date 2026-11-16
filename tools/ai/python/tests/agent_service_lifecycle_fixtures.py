"""Real lifecycle test processes and a controlled listener dependency."""

import multiprocessing
import os
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from sico.service.service_discovery import discover_project
from sico.service.service_management import exchange_service
from sico.service.service_messages import ServiceRequest
from sico.transport.framing import Connection


@contextmanager
def service_process(project, *, idle_seconds=300, environment=None, settled=False):
    parent, child = socket.socketpair()
    command = [sys.executable, str(Path(__file__).absolute().parents[3] / "sico/python/sico-entry"),
               "project-service", "--project-dir", str(project), "--bootstrap-fd",
               str(child.fileno()), "--idle-seconds", str(idle_seconds)]
    process = subprocess.Popen(command, env=environment, pass_fds=(child.fileno(),),
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        start_new_session=True)
    child.close()
    try:
        try:
            row = Connection(parent).receive(deadline=time.monotonic() + 10)
        except BaseException:
            if process.poll() is None:
                process.terminate()
            stderr = process.communicate(timeout=5)[1].decode("utf-8", "replace")
            raise AssertionError("service failed before ready: " + stderr) from None
        assert row["kind"] == "ready", row
        parent.close()
        descriptor = discover_project(project).service
        assert descriptor is not None
        if settled:
            await_empty_service(descriptor)
        yield process, descriptor
    finally:
        parent.close()
        if process.poll() is None:
            process.terminate()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)
            raise AssertionError("Lifecycle service outlived test")


def _paused_listener(project, pipe, entered, release, kind, after):
    import sico.service.service_wire as wire
    from sico.service.service_entry import run_service

    original = wire.management_reply
    negotiation = wire.negotiate

    def reply(method, *args):
        result = original(method, *args) if after else None
        if method == "service." + kind:
            entered.set()
            assert release.wait(10)
        return result if after else original(method, *args)

    wire.management_reply = reply
    if kind == "hello":
        def negotiate(*args):
            entered.set()
            assert release.wait(10)
            return negotiation(*args)
        wire.negotiate = negotiate
    raise SystemExit(run_service(project, bootstrap_fd=pipe.detach()))


@contextmanager
def paused_service(project, *, kind="status", after=False):
    context = multiprocessing.get_context("spawn")
    entered, release = context.Event(), context.Event()
    parent, child = socket.socketpair()
    process = context.Process(target=_paused_listener,
                              args=(project, child, entered, release, kind, after))
    process.start()
    child.close()
    try:
        assert Connection(parent).receive(deadline=time.monotonic() + 10)["kind"] == "ready"
        descriptor = discover_project(project).service
        if kind == "stop":
            await_empty_service(descriptor)
        yield descriptor, entered, release
    finally:
        release.set()
        parent.close()
        if process.is_alive():
            process.terminate()
        process.join(5)
        if process.is_alive():
            process.kill()
            process.join(5)
            raise AssertionError("Paused service outlived test")


def lsf_environment():
    return dict(os.environ, LSB_JOBID="test-job", LSB_QUEUE="test-queue",
                LSF_CLUSTER_NAME="test-cluster", SICO_API_KEY="never-expose-this-key")


def _paused_cleanup(project, pipe, entered, release):
    from contextlib import contextmanager

    import sico.service.service_entry as entry

    original = entry.listening_socket

    @contextmanager
    def listener():
        with original() as resources:
            yield resources
            entered.set()
            assert release.wait(10)

    entry.listening_socket = listener
    raise SystemExit(entry.run_service(project, bootstrap_fd=pipe.detach()))


@contextmanager
def paused_cleanup(project):
    context = multiprocessing.get_context("spawn")
    entered, release = context.Event(), context.Event()
    parent, child = socket.socketpair()
    process = context.Process(target=_paused_cleanup, args=(project, child, entered, release))
    process.start()
    child.close()
    try:
        assert Connection(parent).receive(deadline=time.monotonic() + 10)["kind"] == "ready"
        descriptor = discover_project(project).service
        await_empty_service(descriptor)
        yield descriptor, entered, release
    finally:
        release.set()
        parent.close()
        process.join(5)
        if process.is_alive():
            process.kill()
            process.join(5)
            raise AssertionError("Cleanup service outlived test")


def await_empty_service(descriptor):
    """Stop tests need completed background scans, not merely a ready handshake."""
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        status = exchange_service(descriptor, ServiceRequest(), deadline=deadline).status
        if not any(status.record()["pending"].values()):
            return
        time.sleep(0.01)
    raise AssertionError("Empty service did not complete startup scans")
