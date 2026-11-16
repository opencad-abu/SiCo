"""Local listener fixture for deterministic clock and socket backpressure faults."""

import threading
from contextlib import contextmanager
from types import SimpleNamespace

from sico.service.project_service import ProjectServiceLease
from sico.service.service_allocation import Allocation
from sico.service.service_lifecycle import ServiceLifecycle
from sico.service.service_listener import serve_ready
from sico.service.service_runtime import listening_socket


@contextmanager
def listener_service(project, *, lifecycle=None):
    stopped, failures = threading.Event(), []
    lifecycle = lifecycle or ServiceLifecycle()
    with ProjectServiceLease.acquire(project) as lease, listening_socket() as (listener, endpoint):
        descriptor = lease.publish(endpoint)

        def run():
            try:
                serve_ready(listener, descriptor, stopped, lifecycle, Allocation())
            except BaseException as exc:
                failures.append(exc)

        thread = threading.Thread(target=run, name="test-service-listener")
        thread.start()
        try:
            yield SimpleNamespace(descriptor=descriptor, thread=thread, lifecycle=lifecycle)
        finally:
            stopped.set()
            thread.join(3)
            assert not thread.is_alive() and not failures


class WriteGate:
    """Keep one socket unwritable until released, preserving the real selector state."""

    def __init__(self, sock):
        self.socket = sock
        self.entered, self.release = threading.Event(), threading.Event()

    def fileno(self):
        return self.socket.fileno()

    def send(self, data):
        if not self.release.is_set():
            self.entered.set()
            raise BlockingIOError()
        return self.socket.send(data)

    def recv(self, size):
        return self.socket.recv(size)

    def close(self):
        self.socket.close()
