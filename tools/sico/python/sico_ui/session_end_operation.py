"""Track one captured session end until its original operation reaches a terminal state."""

import time
import uuid

from PyQt5.QtCore import QObject, pyqtSignal

from sico.service.service_errors import is_definite_refusal
from .receipts import DataReceipt


class EndOperation(QObject):
    changed = pyqtSignal()
    finished = pyqtSignal(object)
    RETRIES = 3

    def __init__(self, parent, api, token):
        super().__init__(parent)
        self.api, self.token = api, token
        self.operation_id = uuid.uuid4().hex
        self.state = "authorizing"
        self.error = ""
        self.receipt = DataReceipt(self)
        self._failures = 0
        self._due = 0

    @property
    def pending(self):
        return self.receipt.pending is not None

    def start(self):
        self._watch(self.api.control.ensure, (self.token,), self._authorized)

    def _authorized(self, _view):
        self.state = "unknown"
        self.changed.emit()
        self._request(observe=False)

    def _watch(self, method, args, complete, **kwargs):
        try:
            self.receipt.watch(method(*args, **kwargs), complete, self._failed)
        except (ValueError, OSError, RuntimeError) as exc:
            self._failed(exc)

    def _request(self, *, observe):
        try:
            future = self.api.end_session(self.token, self.operation_id, observe=observe)
            self.receipt.watch(future, self._complete,
                               lambda error: self._failed(error, observing=observe))
        except (ValueError, OSError, RuntimeError) as exc:
            self._failed(exc, observing=observe)

    def _complete(self, result):
        self.state = result.state
        self.error = ""
        self._failures = 0
        self._due = time.monotonic() + .3
        self.changed.emit()
        if self.state in {"ended", "needs_reconcile"}:
            self.finished.emit(self)
        elif self.state == "unknown":
            # Observation alone cannot establish whether an unacknowledged intent ran.
            self.error = "原结束请求尚未确认，请核对原请求"
            self.finished.emit(self)

    def _failed(self, error, *, observing=False):
        self.error = str(error)
        if self.state == "authorizing" or not observing and is_definite_refusal(error):
            self.state = "refused"
        else:
            self._failures += 1
            self._due = time.monotonic() + .5
        self.changed.emit()
        if self.state == "refused" or self._failures >= self.RETRIES:
            self.finished.emit(self)

    def poll(self):
        if (not self.pending and time.monotonic() >= self._due
                and (self.state == "closing" and self._failures < self.RETRIES or
                     self.state == "unknown" and 0 < self._failures < self.RETRIES)):
            self.observe()

    def observe(self):
        if self.pending:
            return
        self._request(observe=True)

    def close(self):
        self.receipt.clear()
