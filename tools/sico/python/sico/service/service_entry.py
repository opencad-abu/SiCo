"""Compose headless project service resources and report bootstrap outcome."""

import signal
import socket
import threading
from contextlib import ExitStack, contextmanager

from ..transport.framing import Connection
from .project_service import ProjectBusy, ProjectServiceLease
from .project_sessions import ProjectSessionOwner
from .service_allocation import capture_allocation
from .service_dispatch import ServiceDispatch
from .service_exit import ServiceExit
from .service_lifecycle import DEFAULT_IDLE_SECONDS, ServiceLifecycle
from .service_listener import serve_ready
from .service_protocol import FRAME_LIMIT
from .service_ready import BOOTSTRAP_PROTOCOL
from .service_runtime import listening_socket


def _report(channel, kind, *, phase="", code=""):
    if channel is None:
        return
    try:
        channel.settimeout(1.0)
        Connection(channel, max_frame=FRAME_LIMIT).send(
            dict(protocol=BOOTSTRAP_PROTOCOL, kind=kind, phase=phase, code=code))
    except OSError:
        # A launcher may detach at any point; it never owns the service lifetime.
        pass
    finally:
        channel.close()


@contextmanager
def _signals(stopped):
    previous = {}
    try:
        for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            previous[number] = signal.signal(number, lambda *_args: stopped.set())
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def run_service(project, *, bootstrap_fd=None, idle_seconds=DEFAULT_IDLE_SECONDS):
    from .service_custody import run
    return run(_run_service, project, bootstrap_fd=bootstrap_fd, idle_seconds=idle_seconds)


def _run_service(project, *, bootstrap_fd=None, idle_seconds=DEFAULT_IDLE_SECONDS, custody=None):
    channel = None if bootstrap_fd is None else socket.socket(fileno=bootstrap_fd)
    if channel is not None:
        channel.set_inheritable(False)
    stopped = threading.Event()
    phase = "ownership"
    exit_guard = ServiceExit(stopped)
    try:
        with _signals(stopped), ExitStack() as resources:
            allocation = capture_allocation()
            lease = resources.enter_context(ProjectServiceLease.acquire(project))
            phase = "runtime"
            listener, endpoint = resources.enter_context(listening_socket())
            phase = "publication"
            lease.mark_runtime(endpoint)
            descriptor = lease.publish(endpoint)
            from .service_custody import publish
            publish(custody, descriptor)
            session_owner = ProjectSessionOwner(project)
            resources.callback(session_owner.close, None)
            dispatch = ServiceDispatch(session_owner, descriptor)
            def drain():
                stopped.set()
                try:
                    dispatch.close()
                    dispatch.wait()
                except BaseException:
                    # Failed cleanup cannot release the lease under live writers.
                    # The process deadline, not stack unwinding, owns this exit.
                    threading.Event().wait()
            resources.callback(drain)
            lifecycle = ServiceLifecycle(
                idle_seconds=idle_seconds, pending=dispatch.pending_work
            )
            if stopped.is_set():
                raise InterruptedError("Service startup interrupted")
            _report(channel, "ready")
            channel = None
            phase = "serving"
            serve_ready(listener, descriptor, stopped, lifecycle, allocation, dispatch)
        return 0
    except ProjectBusy:
        _report(channel, "busy")
        return 2
    except Exception as exc:
        # Report only after ExitStack releases this process's socket and lease.
        code = getattr(exc, "code", type(exc).__name__)
        _report(channel, "failed", phase=phase, code=str(code)[:128])
        return 1
    finally:
        exit_guard.close()
