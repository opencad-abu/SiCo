"""Cancellable preparation thread and receipt lifetime, without backend capabilities."""

import os
import queue
import threading
import time
from concurrent.futures import CancelledError, Future
from contextlib import ExitStack

from .startup_result import StartupFailure


class StartupLifecycle:
    def __init__(self, args, notice, *, environment=None,
                 registration_timeout=30, context_timeout=20):
        self.args, self.notice = args, notice
        self.environment = dict(os.environ if environment is None else environment)
        self.registration_timeout, self.context_timeout = registration_timeout, context_timeout
        self.configuration, self.ready = Future(), Future()
        self.configuration.set_running_or_notify_cancel()
        self.ready.set_running_or_notify_cancel()
        self.phase = "registration"
        self._registration = queue.Queue(maxsize=1)
        self._credential = queue.Queue(maxsize=1)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="copilot-startup", daemon=True)
        self._thread.start()

    def start(self, registration):
        if not self._stop.is_set():
            self._registration.put_nowait(registration)

    def provide_credential(self, value):
        if not self._stop.is_set():
            self._credential.put_nowait(value)

    def _check(self):
        if self._stop.is_set():
            raise CancelledError()

    def _receive(self, channel, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            self._check()
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("Desktop registration timed out")
            try:
                return channel.get(timeout=0.05)
            except queue.Empty:
                pass

    def _run(self):
        try:
            with ExitStack() as resources:
                result = self._prepare(resources)
                self._check()
                self.phase = "ready"
                self.ready.set_result(result)
                self._stop.wait()
        except Exception as exc:
            # 抛出点已经写清阶段与文案的失败不再套一层：窗口规则要原样呈现给用户。
            error = (CancelledError() if self._stop.is_set()
                     else exc if isinstance(exc, StartupFailure)
                     else StartupFailure(self.phase, exc))
            for future in (self.configuration, self.ready):
                if not future.done():
                    future.set_exception(error)
        finally:
            self.environment.clear()

    def close(self):
        self._stop.set()

    def wait(self, timeout=None):
        self._thread.join(timeout)
        return not self._thread.is_alive()
