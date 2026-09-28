"""Origin-scoped Qt callbacks; completion bookkeeping survives page destruction."""

from concurrent.futures import CancelledError, Future
from threading import Event

from PyQt5.QtCore import QObject, QTimer

from sico.service.diagnostics import diagnostics


def outcome(future):
    try:
        return future.result(), None
    except Exception as exc:
        return None, exc


class CommandReceipts(QObject):
    def __init__(self, parent, *, capture, current, closing, refresh, show_error):
        super().__init__(parent)
        self.capture = capture
        self.current = current
        self.closing = closing
        self.refresh = refresh
        self.show_error = show_error
        self.error = ""
        self.pending = []
        self.disposed = False
        self.timer = QTimer(self)
        self.timer.setInterval(25)
        self.timer.timeout.connect(self.poll)
        self.timer.start()
        parent.destroyed.connect(lambda *_: self.dispose())

    def clear_error(self):
        self.error = ""

    def report(self, message):
        self.error = message
        self.show_error(message)

    def dispose(self):
        self.disposed = True
        self.pending.clear()

    def watch(self, future, *, success=None, failure=None, settled=None, scope=None):
        """settled is Qt-free bookkeeping, executed exactly once on completion."""
        scope = scope or self.capture()
        ready = Event()
        if settled is not None:
            def complete(receipt):
                try:
                    settled(*outcome(receipt))
                except Exception as exc:
                    diagnostics.submit(__name__, "Command bookkeeping failed", exc)
                finally:
                    ready.set()
            future.add_done_callback(complete)
        else:
            ready.set()
        if not self.disposed:
            self.pending.append((future, success, failure, scope, ready))
        return future

    def rejected(self, error, **callbacks):
        future = Future()
        future.set_exception(error)
        self.watch(future, **callbacks)
        self.poll()

    def poll(self):
        if self.disposed:
            return
        if self.closing():
            self.pending.clear()
            self.timer.stop()
            return
        pending, self.pending = self.pending, []
        for future, success, failure, scope, ready in pending:
            if not future.done() or not ready.is_set():
                self.pending.append((future, success, failure, scope, ready))
                continue
            # Recompute controls from the current page, including stale commands
            # whose bookkeeping just released a duplicate-submission guard.
            try:
                self.refresh()
            except Exception as exc:
                diagnostics.submit(__name__, "Command controls refresh failed", exc)
            if not self.current(scope):
                continue
            result, error = outcome(future)
            try:
                if error is not None:
                    if isinstance(error, CancelledError):
                        error = RuntimeError("操作已取消，未接收的输入仍保留")
                    if failure and failure(error) is False:
                        continue
                    self.report(str(error))
                elif success:
                    success(result)
            except Exception as exc:
                diagnostics.submit(__name__, "Command presentation callback failed", exc)
                if self.current(scope):
                    self.report(str(exc))
