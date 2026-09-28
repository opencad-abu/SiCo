"""Controlled GUI controllers and confirmation dialog for source tests."""

from __future__ import annotations

from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtWidgets import QMessageBox


class FakeController(QObject):
    busyChanged = pyqtSignal(bool)
    snapshotReady = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self.submissions: list[object] = []
        self.busy = False
        self.shutdown_calls = 0

    def submit(self, queue: str | None, force_topology: bool = False) -> int:
        self.submissions.append((queue, force_topology) if force_topology else queue)
        self.busy = True
        self.busyChanged.emit(True)
        return len(self.submissions)

    def shutdown(self, _timeout_ms: int = 2_000) -> bool:
        self.shutdown_calls += 1
        self.busy = False
        return True


class FakeJobActionController(QObject):
    busyChanged = pyqtSignal(bool)
    succeeded = pyqtSignal(str)
    failed = pyqtSignal(str, str)

    def __init__(self) -> None:
        super().__init__()
        self.kill_calls: list[str] = []
        self.busy = False
        self.shutdown_calls = 0

    def kill_job(self, job_id: str) -> bool:
        if self.busy:
            return False
        self.kill_calls.append(job_id)
        self.busy = True
        self.busyChanged.emit(True)
        return True

    def shutdown(self, _timeout_ms: int = 2_000) -> bool:
        self.shutdown_calls += 1
        self.busy = False
        self.busyChanged.emit(False)
        return True


class FakeValidationController(QObject):
    busyChanged = pyqtSignal(bool)
    succeeded = pyqtSignal(str, object)
    failed = pyqtSignal(str)

    def __init__(self, *, auto_complete: bool = True) -> None:
        super().__init__()
        self.auto_complete = auto_complete
        self.busy = False
        self.requests: list[tuple[str, str | None]] = []
        self.shutdown_calls = 0

    def validate(self, queue: str, host: str | None = None) -> bool:
        if self.busy:
            return False
        self.busy = True
        self.requests.append((queue, host))
        self.busyChanged.emit(True)
        if self.auto_complete:
            self.complete()
        return True

    def complete(self) -> None:
        queue, host = self.requests[-1]
        self.busy = False
        self.busyChanged.emit(False)
        self.succeeded.emit(queue, host)

    def shutdown(self, _timeout_ms: int = 2_000) -> bool:
        self.shutdown_calls += 1
        self.busy = False
        return True


class FakeConfirm:
    """自绘确认框替身：按 accept_kill 返回取消或确认，并记录放到框里的文案。"""

    accept_kill = False
    last = None

    def __init__(self, title, text, _parent=None) -> None:
        self.title = title
        self.text = text
        self.choices = []
        self._default = None
        FakeConfirm.last = self

    def setObjectName(self, _name: str) -> None:
        pass

    def add_choice(self, key, text, *, default=False, escape=False, danger=False):
        self.choices.append((key, text))
        if default:
            self._default = key
        return None

    def ask(self):
        if self.accept_kill:
            return "kill"
        return self._default or "cancel"
