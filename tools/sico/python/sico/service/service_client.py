"""Async client pinned to one discovered project/service; owns only local waits."""

import threading
import uuid

from .service_completion import ServiceCompletion
from .service_discovery import ServiceDescriptor
from .service_errors import ServiceCapacityError
from .service_management import exchange_service
from .service_messages import ServiceRequest
from .service_protocol import require_id
from .service_waiter import ServiceWaiter

MAX_IN_FLIGHT = 16


class ServiceClient:
    def __init__(self, descriptor, *, client_id=None):
        if type(descriptor) is not ServiceDescriptor:
            raise TypeError("A discovered ServiceDescriptor is required")
        self.__descriptor = descriptor
        self.__client_id = require_id(uuid.uuid4().hex if client_id is None else client_id)
        self.__detached = threading.Event()
        # This lock protects only bounded local bookkeeping; never transport or encoding.
        self.__lock = threading.Lock()
        self.__pending = set()

    def request(self, request=None, *, timeout=5.0):
        request = ServiceRequest() if request is None else request
        if type(request) is not ServiceRequest:
            raise TypeError("A ServiceRequest is required")
        with self.__lock:
            if self.__detached.is_set():
                raise RuntimeError("Service client is detached")
            if len(self.__pending) >= MAX_IN_FLIGHT:
                raise ServiceCapacityError("Service client request limit reached")
            waiter = _Exchange(self.__descriptor, self.__client_id, request, timeout, self._retire)
            self.__pending.add(waiter)
            return ServiceCompletion(waiter, self.__detached)

    def close(self):
        self.__detached.set()
        with self.__lock:
            pending = tuple(self.__pending)
        for waiter in pending:
            waiter.close()

    def _retire(self, waiter):
        with self.__lock:
            self.__pending.discard(waiter)


class _Exchange(ServiceWaiter):
    def __init__(self, descriptor, client_id, request, timeout, finished):
        super().__init__(lambda deadline: exchange_service(
            descriptor, request, deadline=deadline, cancelled=self._cancelled,
            client_id=client_id), timeout=timeout, finished=finished)
