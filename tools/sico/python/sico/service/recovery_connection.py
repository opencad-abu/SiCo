"""Explicit asynchronous discovery for a new observing window after service replacement."""

import threading
from contextlib import ExitStack

from .service_waiter import ServiceWaiter


class RecoveryConnection(ServiceWaiter):
    def __init__(self, project, session_id):
        self._resources = ExitStack()
        self._resource_lock = threading.Lock()
        super().__init__(lambda deadline: self._open(project, session_id, deadline), timeout=30)

    def _await(self, receipt, deadline):
        while not receipt.done():
            self._check(deadline)
            self._wake.wait(.02)
        self._check(deadline)
        return receipt.result()

    def _open(self, project, session_id, deadline):
        from .remote_frontend import RemoteFrontend
        from .service_startup import ServiceStartup

        with ExitStack() as pending:
            startup = ServiceStartup(project)
            pending.callback(startup.close)
            descriptor = self._await(startup.ready, deadline)
            api = RemoteFrontend(descriptor, project, session_id)
            pending.callback(api.close_desktop)
            frontend = self._await(api.recovery.open_history(session_id), deadline)
            with self._resource_lock:
                self._check(deadline)
                self._resources = pending.pop_all()
            return frontend

    def close(self):
        super().close()
        with self._resource_lock:
            self._resources.close()
