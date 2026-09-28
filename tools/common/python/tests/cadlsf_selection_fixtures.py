"""Qt harness for selector tests using the narrow selection interface."""

from __future__ import annotations

import os
from pathlib import Path
import time
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QMainWindow,
    QTableView,
    QVBoxLayout,
    QWidget,
)
from cadlsf.collector import CollectorConfig
from cadlsf.gui.models import HostFilterProxyModel, HostTableModel
from cadlsf.gui.selector import SelectorActions
from cadlsf.gui.selection_state import selection_state
from cadlsf.model import ClusterSnapshot, HostInfo, QueueInfo

_APPLICATION = None


@pytest.fixture(scope="module")
def application() -> QApplication:
    global _APPLICATION
    _APPLICATION = QApplication.instance() or QApplication([])
    yield _APPLICATION


class FakeRefreshController:
    busy = False


class FakeValidationController(QObject):
    busyChanged = pyqtSignal(bool)
    succeeded = pyqtSignal(str, object)
    failed = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self.busy = False
        self.requests: list[tuple[str, str | None]] = []
        self.shutdown_calls = 0

    def validate(self, queue: str, host: str | None = None) -> bool:
        if self.busy:
            return False
        self.requests.append((queue, host))
        self.busy = True
        self.busyChanged.emit(True)
        return True

    def complete(self) -> None:
        queue, host = self.requests[-1]
        self.busy = False
        self.busyChanged.emit(False)
        self.succeeded.emit(queue, host)

    def reject(self, message: str) -> None:
        self.busy = False
        self.busyChanged.emit(False)
        self.failed.emit(message)

    def shutdown(self, _timeout_ms: int = 2_000) -> bool:
        self.shutdown_calls += 1
        self.busy = False
        self.busyChanged.emit(False)
        return True


def _snapshot() -> ClusterSnapshot:
    return ClusterSnapshot(
        status="ready",
        collected_at="2026-08-20T12:00:00Z",
        user="demo",
        selected_queue="normal",
        queues=(QueueInfo("normal", "Open:Active", True, 20, 4, 16),),
        hosts=(
            HostInfo("node-a", "ok", True),
            HostInfo("node-b", "ok", True),
        ),
    )


class SelectorWindow(QMainWindow):
    def __init__(self, output: Path, validation: FakeValidationController) -> None:
        super().__init__()
        self._collector_config = CollectorConfig()
        self._snapshot = _snapshot()
        self._status_kind = "ready"
        self._closing = False
        self.controller = FakeRefreshController()
        self.statuses: list[tuple[str, str, str]] = []
        self.close_calls = 0
        central = QWidget(self)
        layout = QVBoxLayout(central)
        self.queue_combo = QComboBox(central)
        self.queue_combo.addItem("normal")
        layout.addWidget(self.queue_combo)
        self.host_model = HostTableModel(self)
        self.host_model.set_rows(self._snapshot.hosts)
        self.host_proxy = HostFilterProxyModel(self)
        self.host_proxy.setSourceModel(self.host_model)
        self.host_table = QTableView(central)
        self.host_table.setModel(self.host_proxy)
        layout.addWidget(self.host_table)
        self.selector = SelectorActions(
            output,
            read_selection=self.read_selection,
            show_status=self._set_status,
            close=self.close,
            validation_controller=validation,
        )
        self.selector.add_to_layout(layout, central)
        self.setCentralWidget(central)
        self.host_table.selectRow(0)
        self.selector.connect()

    def read_selection(self):
        index = self.host_proxy.mapToSource(self.host_table.currentIndex())
        host = self.host_model.rows[index.row()] if index.isValid() else None
        return selection_state(
            self._snapshot,
            queue=self.queue_combo.currentText().strip(),
            host=host,
            busy=self.controller.busy,
            status=self._status_kind,
            closing=self._closing,
        )

    def _set_status(self, kind: str, label: str, diagnostic: str = "") -> None:
        self._status_kind = kind
        self.statuses.append((kind, label, diagnostic))

    def close(self) -> bool:
        self.close_calls += 1
        self._closing = True
        return True


def _wait_until(application: QApplication, predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        application.processEvents()
        time.sleep(0.005)
    assert predicate()
