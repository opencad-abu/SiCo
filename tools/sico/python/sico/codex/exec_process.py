"""One managed exec-server process using the shared RPC and local supervisor.

This is a process transport, not a command-boundary or terminal backend. It
never infers command completion from output, prompts or process-write receipts.
"""

import base64
import queue
import threading
import time
import uuid

from .exec_events import ProcessEvents
from .exec_policy import process_environment, sandbox_request
from .rpc import AppServer, RpcError
from .rpc_write import deadline_after


class ManagedExecProcess:
    """Trusted composition supplies the fixed executable and server environment.

    start is attempted once. A write is never retried after uncertain delivery.
    read exposes an ordered process stream; it does not assign bytes to commands.
    close requires a successful supervisor cleanup even if it exited earlier.
    """

    def __init__(self, executable, server_environment, *, cwd, home, access, writable_roots=()):
        self._sandbox = sandbox_request(access, cwd, home=home, writable_roots=writable_roots)
        self._server_environment = process_environment(server_environment)
        self._executable, self._cwd = str(executable), str(cwd)
        self._lock = threading.RLock()
        self._stopped = threading.Event()
        self._rpc = None
        self._attempted = False
        self._process_id = uuid.uuid4().hex
        self._events = ProcessEvents(self._process_id)
        self._gap_since = None
        self._write_id = 0
        self._exited = False
        self._stream_closed = False
        self._startup_uncertain = False

    def start(self, argv, environment):
        if (not isinstance(argv, (list, tuple)) or not argv
                or any(not isinstance(arg, str) or "\0" in arg for arg in argv) or not argv[0]):
            raise ValueError("Invalid managed process argv")
        environment = process_environment(environment)
        # The managed launcher resolves bwrap in the target PATH. Empty or
        # unavailable PATH must fail, never cause an unsandboxed fallback.
        with self._lock:
            if self._attempted or self._stopped.is_set():
                raise RuntimeError("Managed process creation is occupied or closed")
            self._attempted = True
            try:
                self._startup_uncertain = True
                self._rpc = AppServer([self._executable, "exec-server", "--listen", "stdio://"],
                                      self._server_environment, self._cwd, protocol="exec-server")
                self._startup_uncertain = False
                status = self._rpc.request("environment/status", {}, timeout=5)
                if status != {"status": "ready"}:
                    raise RpcError("Execution environment is not ready")
                result = self._rpc.request("process/start", dict(
                    processId=self._process_id, argv=list(argv), cwd=self._sandbox["cwd"],
                    envPolicy=dict(inherit="none", ignoreDefaultExcludes=False,
                                   exclude=[], set={}, includeOnly=[]), env=environment,
                    tty=False, pipeStdin=True, sandbox=self._sandbox), timeout=5)
                if result != {"processId": self._process_id, "sandboxType": "linuxSeccomp"}:
                    raise RpcError("Managed Linux process scope was not confirmed")
            except BaseException:
                self.close()
                raise

    def _available(self):
        if self._rpc is None or self._stopped.is_set():
            raise RuntimeError("Managed process is not available")
        return self._rpc

    def write(self, data, *, timeout=5):
        """Accepted means stdin delivery only; callers must observe execution separately."""
        if not isinstance(data, bytes) or not 1 <= len(data) <= 65536:
            raise ValueError("Managed stdin requires 1..65536 bytes")
        deadline_after(timeout)
        if timeout > 5:
            raise ValueError("Managed stdin timeout exceeds five seconds")
        with self._lock:
            rpc = self._available()
            if self._exited:
                raise RuntimeError("Managed process already exited")
            self._write_id += 1
            try:
                result = rpc.request("process/write", dict(processId=self._process_id,
                    writeId=str(self._write_id), chunk=base64.b64encode(data).decode("ascii")), timeout)
                if result != {"status": "accepted"}:
                    raise RpcError("Managed stdin acceptance was not confirmed")
            except BaseException:
                self.close()
                raise

    def read(self, *, timeout=.1):
        """Return the next validated process event, or None after a finite wait."""
        deadline_after(timeout)
        if timeout > 1:
            raise ValueError("Managed read wait exceeds one second")
        with self._lock:
            rpc = self._available()
            if self._stream_closed:
                return None
            try:
                result = self._events.pop()
                if result is None:
                    try:
                        message = rpc.next(timeout)
                    except queue.Empty:
                        message = None
                    if message is not None:
                        self._events.push(message)
                    result = self._events.pop()
                self._check_gap()
                if result is None:
                    return None
                if (result.kind == "exited" and self._exited
                        or result.kind == "closed" and not self._exited):
                    raise ValueError("Invalid exec-server lifecycle order")
                self._exited |= result.kind == "exited"
                self._stream_closed |= result.kind == "closed"
                return result
            except BaseException:
                self.close()
                raise

    def _check_gap(self):
        if not self._events.waiting:
            self._gap_since = None
        elif self._gap_since is None:
            self._gap_since = time.monotonic()
        elif time.monotonic() - self._gap_since >= 1:
            raise RpcError("Exec-server event sequence gap did not resolve")

    def terminate(self):
        """Request process termination; exit and output closure remain observations."""
        with self._lock:
            rpc = self._available()
            try:
                result = rpc.request("process/terminate", {"processId": self._process_id}, timeout=5)
                if (not isinstance(result, dict) or set(result) != {"running"}
                        or type(result["running"]) is not bool):
                    raise RpcError("Invalid process termination receipt")
            except BaseException:
                self.close()
                raise

    def observe_process(self, namespace_pid, namespace, executable):
        """Verify a namespace hint against this transport's owned host processes."""
        with self._lock:
            return self._available().observe_process(namespace_pid, namespace, executable)

    def close(self):
        self._stopped.set()
        with self._lock:
            if self._rpc is not None:
                self._rpc.close(require_confirmed=True)
            elif self._startup_uncertain:
                raise RuntimeError("Execution server startup cleanup requires reconciliation")
