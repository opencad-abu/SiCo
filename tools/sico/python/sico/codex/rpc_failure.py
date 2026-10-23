"""Stable app-server failure records and private evidence emission."""

import time

from .. import diagnostics


class RpcFailureSupport:
    @staticmethod
    def failure_record(code, message, exception, *, frame_bytes=None):
        value = dict(code=code, message=message, exception=exception)
        if frame_bytes is not None:
            value["frame_bytes"] = frame_bytes
        return value

    def process_failure(self, wait=0):
        process = getattr(self, "_process", None)
        report = getattr(process, "failure", None)
        return report(wait) if callable(report) else {}

    def emit(self, event, **fields):
        diagnostic = getattr(self, "_diagnostic", None)
        if diagnostic is not None:
            diagnostic.emit(event, **fields)

    def emit_failure(self, failure):
        observed = self.process_failure(0)
        self.emit(
            "codex.rpc.failed",
            category=failure["code"],
            stage="read",
            exception=failure.get("exception"),
            exit_code=observed.get("exit_code"),
            stderr=observed.get("stderr"),
            frame_bytes=failure.get("frame_bytes"),
            sent_bytes=failure.get("sent_bytes"),
        )

    def retain_failure(self, failure):
        """Keep the first reason this connection became unusable."""
        lock = getattr(self, "_failure_lock", None)
        if lock is None:
            if getattr(self, "_failure", None) is None:
                self._failure = failure
        else:
            with lock:
                if getattr(self, "_failure", None) is None:
                    self._failure = failure

    def failure_reason(self, failure):
        if failure["code"] in {
                diagnostics.PROCESS_EXIT, diagnostics.IMPORT_FAILED, diagnostics.AUTH_FAILED}:
            return self.disconnect_reason()
        return failure["message"]

    def disconnect_reason(self):
        """Explain an observed disconnection with the exit evidence we hold.

        The reason reaches the persisted task diagnostic, so "why did the
        app-server die" stays answerable after the process is gone.
        """
        if self.closed_by_client:
            return "Codex app-server disconnected (client closed)"
        code = getattr(self, "exit", None)
        if code is None:
            observed_exit = getattr(
                getattr(self, "_process", None), "exit_code", None)
            code = observed_exit() if callable(observed_exit) else None
            poll = getattr(self.child, "poll", None)
            if code is None:
                code = poll() if poll is not None else None
            deadline = time.monotonic() + 1.0
            while code is None and poll is not None and time.monotonic() < deadline:
                time.sleep(0.05)
                code = observed_exit() if callable(observed_exit) else None
                if code is None:
                    code = poll()
        if code is None:
            return "Codex app-server disconnected (process still exiting)"
        if code < 0:
            return "Codex app-server disconnected (signal %d)" % -code
        return "Codex app-server disconnected (exit %d)" % code
