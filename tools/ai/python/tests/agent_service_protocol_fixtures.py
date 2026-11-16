"""Raw authenticated connections and deterministic in-process protocol responders."""

import threading
import time
from contextlib import contextmanager

from sico.service.project_service import ProjectServiceLease
from sico.service.service_allocation import Allocation
from sico.service.service_handshake import accept_ready, hello_message
from sico.service.service_lifecycle import ServiceLifecycle
from sico.service.service_peer import service_connection
from sico.service.service_runtime import listening_socket
from sico.service.service_wire import ServiceWire
from sico.transport.framing import Connection, encode, strict_json


@contextmanager
def raw_channel(descriptor, **hello_options):
    with service_connection(descriptor, deadline=time.monotonic() + 2) as connection:
        hello = hello_message(descriptor, **hello_options)
        connection.send(hello)
        reply = connection.receive(deadline=time.monotonic() + 2)
        yield connection, accept_ready(reply, hello)


@contextmanager
def responder(project, transform):
    failures = []
    with ProjectServiceLease.acquire(project) as lease, listening_socket() as (listener, endpoint):
        descriptor = lease.publish(endpoint)
        lifecycle = ServiceLifecycle()

        def respond():
            try:
                listener.settimeout(3)
                with listener.accept()[0] as sock:
                    connection, wire = Connection(sock), ServiceWire()
                    hello = connection.receive(deadline=time.monotonic() + 2)
                    connection.send(wire.reply(hello, descriptor, lifecycle, Allocation()))
                    request = connection.receive(deadline=time.monotonic() + 2)
                    reply = wire.reply(request, descriptor, lifecycle, Allocation())
                    reply = transform(strict_json(encode(reply)[4:]))
                    if reply is not None:
                        connection.send(reply)
            except Exception as exc:
                failures.append(exc)

        thread = threading.Thread(target=respond)
        thread.start()
        try:
            yield descriptor, listener, lifecycle
        finally:
            thread.join(4)
            assert not thread.is_alive() and not failures
