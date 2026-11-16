"""Explicitly controlled test clients for the migrated service admission contract."""

import threading
import uuid
from dataclasses import asdict, replace

from sico.service.service_channel import service_channel
from sico.service.service_client import ServiceClient
from sico.service.service_completion import ServiceCompletion
from sico.service.service_messages import ServiceRequest
from sico.service.service_waiter import ServiceWaiter
from sico.transport.framing import ProtocolError


def controlled_exchange(descriptor, request, deadline, cancelled=None):
    """Each fixture submission explicitly observes/takes control on its own socket."""
    with service_channel(descriptor, client_id=uuid.uuid4().hex, deadline=deadline,
                         cancelled=cancelled) as channel:
        params = dict(address=request.params["address"], action="status", generation=0,
                      previous=None)
        try:
            view = channel.exchange(ServiceRequest("session.control", params=params),
                                    deadline=deadline, cancelled=cancelled).status
            params.update(action="acquire" if view.state == "available" else "takeover",
                          generation=view.generation)
            view = channel.exchange(ServiceRequest("session.control", params=params),
                                    deadline=deadline, cancelled=cancelled).status
            request = replace(request, control=asdict(view.claim))
        except ProtocolError:
            raise
        except ValueError:
            pass  # Old runtime/closed session still permits lookup of an existing input.
        return channel.exchange(request, deadline=deadline, cancelled=cancelled)


class ControlledInputClient(ServiceClient):
    def __init__(self, descriptor):
        super().__init__(descriptor)
        self.descriptor = descriptor
        self.waiters = []

    def request(self, request=None, *, timeout=5):
        if request is None or request.method != "session.submit":
            return super().request(request, timeout=timeout)
        detached = threading.Event()
        waiter = ServiceWaiter(lambda deadline: controlled_exchange(
            self.descriptor, request, deadline, detached.is_set), timeout=timeout)
        waiter.detached = detached
        self.waiters.append(waiter)
        return ServiceCompletion(waiter, detached)

    def close(self):
        for waiter in self.waiters:
            waiter.detached.set()
            waiter.close()
        super().close()
