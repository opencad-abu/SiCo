"""Own one supervised local process tree, independent of its application protocol.

The owner closes stdin and the lifetime control pipe, then waits for the
supervisor to reap descendants. The consumer owns stdout: stream readers must
stop before closing it. Scheduler jobs still need separate reconciliation.
"""

import os
import subprocess
import sys
import threading
import time

from . import diagnostics
from .installation import python_path
from .process_identity import observe_descendant, process_identity
from sicoprocess import require_subreaper


class ProcessError(RuntimeError):
    """A process failure carrying a stable category and private evidence ID."""

    def __init__(self, message, *, code, diagnostic_id=None, exit_code=None):
        super().__init__(message)
        self.code = code
        self.diagnostic_id = diagnostic_id
        self.exit_code = exit_code


class StderrCapture:
    """Drain a child stderr pipe continuously while retaining only its tail."""

    def __init__(self, stream, limit=diagnostics.MAX_STDERR_BYTES):
        self.stream = stream
        self.limit = max(0, int(limit))
        self.data = bytearray()
        self.thread = threading.Thread(target=self._run, name="sico-stderr", daemon=True)
        self.thread.start()

    def _run(self):
        try:
            while True:
                chunk = self.stream.read(65536)
                if not chunk:
                    break
                self.data.extend(chunk)
                if len(self.data) > self.limit:
                    del self.data[:-self.limit]
        except (OSError, ValueError):
            pass

    def text(self, wait=0.0):
        if wait and self.thread.is_alive():
            self.thread.join(wait)
        return bytes(self.data).decode("utf-8", errors="replace")

    def close(self):
        try:
            self.stream.close()
        except (OSError, ValueError):
            pass


class LocalProcess:
    def __init__(self, command, environment, cwd, *, diagnostic=None,
                 component="process", cleanup_timeout=2):
        require_subreaper()
        environment = dict(environment)
        environment["PYTHONPATH"] = python_path()
        self._diagnostic = diagnostic or diagnostics.DiagnosticLog.disabled(component)
        self.diagnostic_id = self._diagnostic.correlation_id
        self.component = component
        self.cleanup_timeout = cleanup_timeout
        self._lock = threading.Lock()
        self._closed = False
        child_control, self._control = os.pipe()
        try:
            self._status, child_status = os.pipe()
        except BaseException:
            os.close(self._control)
            os.close(child_control)
            raise
        self._exit_status = None
        self._stderr_capture = None
        try:
            self.child = subprocess.Popen(
                [sys.executable, "-s", "-c",
                 "import sys; from sicoprocess import run; "
                 "raise SystemExit(run(int(sys.argv[1]), sys.argv[3:], "
                 "status_fd=int(sys.argv[2])))",
                 str(child_control), str(child_status), *command],
                cwd=cwd, env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, bufsize=0,
                pass_fds=(child_control, child_status),
                start_new_session=True)
            self._stderr_capture = StderrCapture(self.child.stderr)
            self._emit("process.started", stage="spawn", pid=self.child.pid)
        except BaseException as exc:
            self._emit("process.start_failed", category=diagnostics.PROCESS_SPAWN_FAILED,
                       stage="spawn", exception=type(exc).__name__)
            os.close(self._control)
            os.close(self._status)
            raise
        finally:
            os.close(child_control)
            os.close(child_status)
        try:
            self._process_start = int(process_identity(self.child.pid)["start"])
        except BaseException:
            try:
                self.close(require_confirmed=True)
            finally:
                self.child.stdout.close()
            raise

    def observe_process(self, namespace_pid, namespace, executable):
        """Read a live descendant's host identity without exposing custody handles."""
        with self._lock:
            if self._control < 0 or self.child.poll() is not None:
                raise RuntimeError("Local process owner is no longer active")
            return observe_descendant(self.child.pid, self._process_start,
                                      namespace_pid, namespace, executable)

    def _emit(self, event, **fields):
        diagnostic = getattr(self, "_diagnostic", None)
        if diagnostic is not None:
            diagnostic.emit(event, **fields)

    def stderr_text(self, wait=0.0):
        capture = getattr(self, "_stderr_capture", None)
        if capture is None:
            return ""
        return capture.text(wait)

    def _read_exit_status(self):
        if not hasattr(self, "_status") or self._status is None:
            return getattr(self, "_exit_status", None)
        try:
            raw = os.read(self._status, 128)
        finally:
            os.close(self._status)
            self._status = None
        value = raw.decode("ascii", errors="ignore").strip()
        if not value or value == "stopped":
            return getattr(self, "_exit_status", None)
        try:
            self._exit_status = int(value)
        except ValueError:
            self._exit_status = None
        return self._exit_status

    def _refresh_exit(self, wait=0.0):
        child = getattr(self, "child", None)
        if child is None:
            return None
        deadline = time.monotonic() + max(0.0, wait)
        while child.poll() is None and time.monotonic() < deadline:
            time.sleep(.01)
        if child.poll() is not None and hasattr(self, "_status") and self._status is not None:
            self._read_exit_status()
        return getattr(self, "_exit_status", None)

    def exit_code(self):
        if (hasattr(self, "_status") and self._status is not None
                and getattr(getattr(self, "child", None), "poll", lambda: None)() is not None):
            self._read_exit_status()
        return getattr(self, "_exit_status", None)

    def wait(self, timeout=None):
        """Wait for the supervisor and return the supervised command's status."""
        self.child.wait(timeout=timeout)
        if hasattr(self, "_status") and self._status is not None:
            self._read_exit_status()
        return getattr(self, "_exit_status", None)

    def failure(self, wait=0.0):
        """Return the observed exit category without exposing raw stderr."""
        child = getattr(self, "child", None)
        code = self._refresh_exit(wait)
        if code is None:
            supervisor = getattr(child, "returncode", None)
            code = supervisor if supervisor not in (None, 0) else None
        text = self.stderr_text(wait)
        category = diagnostics.classify_text(text) if text else None
        if category is None and code not in (None, 0):
            category = diagnostics.PROCESS_EXIT
        return {
            "code": category,
            "diagnostic_id": getattr(self, "diagnostic_id", None),
            "exit_code": code,
            "stderr": text,
        }

    def close(self, *, require_confirmed=False):
        """Strict consumers require supervisor cleanup, even after a prior exit."""
        if getattr(self, "_closed", False):
            if getattr(self, "_cleanup_error", None) is not None:
                raise self._cleanup_error
            if require_confirmed and self.child.returncode != 0:
                raise ProcessError("Local descendant cleanup was not confirmed",
                    code=diagnostics.CLEANUP_UNCONFIRMED,
                    diagnostic_id=getattr(self, "diagnostic_id", None),
                    exit_code=self.child.returncode)
            return
        with self._lock:
            # A supervisor that already exited before this close (a crash) is
            # disconnect evidence for existing consumers. Strict retirement
            # callers must not mistake that crash for descendant cleanup.
            exited_before_close = self.child.poll() is not None
            try:
                self.child.stdin.close()
            except (OSError, ValueError):
                pass
            if self._control >= 0:
                os.close(self._control)
                self._control = -1
            try:
                self.child.wait(timeout=getattr(self, "cleanup_timeout", 2))
            except subprocess.TimeoutExpired:
                self._closed = True
                stderr = self.stderr_text(.1)
                self._emit("process.cleanup_unconfirmed",
                           category=diagnostics.CLEANUP_UNCONFIRMED,
                           stage="cleanup", cleanup="timeout",
                           exception="TimeoutExpired", exit_code=self.child.poll(),
                           stderr=stderr)
                if hasattr(self, "_status") and self._status is not None:
                    os.close(self._status)
                    self._status = None
                self._close_stderr()
                self._cleanup_error = ProcessError(
                    "Local descendants still require reconciliation",
                    code=diagnostics.CLEANUP_UNCONFIRMED,
                    diagnostic_id=getattr(self, "diagnostic_id", None),
                    exit_code=self.child.poll(),
                )
                raise self._cleanup_error from None
            self._closed = True
            self._refresh_exit(0)
            stderr = self.stderr_text(.1)
            self._close_stderr()
            status = self._refresh_exit(0)
            has_status_pipe = hasattr(self, "_status")
            supervisor_failure = (has_status_pipe and self.child.returncode != 0
                                  and status is None)
            legacy_failure = (not has_status_pipe and self.child.returncode != 0
                              and not exited_before_close)
            if supervisor_failure or legacy_failure or (require_confirmed and self.child.returncode != 0):
                self._emit("process.cleanup_unconfirmed",
                           category=diagnostics.CLEANUP_UNCONFIRMED,
                           stage="cleanup", cleanup="nonzero_exit",
                           exception="ProcessExit", exit_code=self.child.returncode,
                           stderr=stderr)
                self._cleanup_error = ProcessError(
                    "Local descendant cleanup was not confirmed",
                    code=diagnostics.CLEANUP_UNCONFIRMED,
                    diagnostic_id=getattr(self, "diagnostic_id", None),
                    exit_code=self.child.returncode,
                )
                raise self._cleanup_error
            observed = status if status is not None else (
                self.child.returncode if not has_status_pipe else None)
            category = (diagnostics.classify_text(stderr)
                        if observed not in (None, 0) and stderr
                        else diagnostics.PROCESS_EXIT
                        if observed not in (None, 0) else diagnostics.PROCESS_STOPPED)
            self._emit("process.exited", category=category, stage="exit",
                       cleanup="observed_exit" if exited_before_close else "confirmed",
                       exit_code=observed, stderr=stderr)

    def _close_stderr(self):
        capture = getattr(self, "_stderr_capture", None)
        if capture is not None:
            capture.close()
