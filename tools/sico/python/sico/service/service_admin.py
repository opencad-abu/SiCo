"""Asynchronous lifecycle administration; never starts a service or retargets a stop."""

from pathlib import Path

from .service_discovery import discover_project
from .service_management import exchange_service
from .service_messages import ServiceRequest
from .service_waiter import ServiceWaiter


class ServiceAdmin(ServiceWaiter):
    def __init__(self, project, *, stop=None, timeout=5.0):
        if not Path(project).is_absolute():
            raise ValueError("Service administration requires an absolute project directory")
        super().__init__(lambda deadline: self._query(project, stop, deadline), timeout=timeout)

    def _query(self, project, stop, deadline):
        self._check(deadline)
        found = discover_project(project)
        self._check(deadline)
        if stop is not None and found.service != stop:
            return dict(state="generation_changed", service=None)
        if found.state != "local_candidate":
            return dict(state=found.state, service=None)
        response = exchange_service(
            found.service, ServiceRequest("service.stop" if stop is not None else "service.status"),
            deadline=deadline, cancelled=self._cancelled).status.record()
        return dict(state=response["state"], service=found.service, status=response)
