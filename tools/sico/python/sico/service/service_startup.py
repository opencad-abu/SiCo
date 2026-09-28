"""Asynchronous discover/start/readiness waiter; cancellation only detaches."""

import socket
import time
from pathlib import Path

from ..transport.framing import Connection, ProtocolError
from .service_discovery import discover_project
from .service_handshake import ServiceStopping
from .service_process import spawn_service
from .service_protocol import FRAME_LIMIT
from .service_ready import BOOTSTRAP_PROTOCOL, probe_ready
from .service_waiter import ServiceWaiter


class ServiceStartupError(RuntimeError):
    def __init__(self, phase, code):
        self.phase, self.code = phase, code
        super().__init__("Project service startup failed: " + phase + "/" + code)


class ServiceStartup(ServiceWaiter):
    """Qt may construct/close and poll ready; all project I/O stays on the worker."""

    def __init__(self, project, *, timeout=30.0):
        if not Path(project).is_absolute():
            raise ValueError("Project service requires an explicit absolute project directory")
        super().__init__(lambda deadline: self._prepare(project, deadline), timeout=timeout)

    def _bootstrap(self, project, deadline):
        with spawn_service(project) as channel:
            try:
                row = Connection(channel, max_frame=FRAME_LIMIT).receive(
                    deadline=deadline, cancelled=self._cancelled)
            except EOFError as exc:
                raise ServiceStartupError("bootstrap", "channel_disconnected") from exc
        if (set(row) != {"protocol", "kind", "phase", "code"}
                or row.get("protocol") != BOOTSTRAP_PROTOCOL
                or row.get("kind") not in ("ready", "busy", "failed")
                or not isinstance(row.get("phase"), str) or len(row["phase"]) > 32
                or not isinstance(row.get("code"), str) or len(row["code"]) > 128):
            raise ProtocolError("Invalid service bootstrap response")
        if row["kind"] == "failed":
            raise ServiceStartupError(row["phase"], row["code"])
        return row["kind"]

    def _prepare(self, project, deadline):
        reported_ready = False
        while True:
            self._check(deadline)
            found = discover_project(project)
            self._check(deadline)
            if found.state == "local_candidate":
                try:
                    return probe_ready(found.service, deadline=min(deadline, time.monotonic() + 1),
                                       cancelled=self._cancelled)
                except ServiceStopping:
                    reported_ready = False
                except (ConnectionError, FileNotFoundError, TimeoutError, socket.timeout, EOFError):
                    # Publication may precede the accept loop. Never replace a busy owner.
                    pass
            elif found.state in {"absent", "inactive"}:
                if reported_ready:
                    raise ServiceStartupError("bootstrap", "owner_exited")
                reported_ready = self._bootstrap(project, deadline) == "ready"
            elif found.state in {"other_host", "unverified"}:
                raise ServiceStartupError("discovery", found.state)
            self._wake.wait(0.05)
