"""Qt application and immutable LSF snapshot fixtures."""

from __future__ import annotations

import os
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication
from cadlsf.model import ClusterSnapshot, Diagnostic, HostInfo, JobInfo, QueueInfo

_APPLICATION = None


@pytest.fixture(scope="module")
def application() -> QApplication:
    global _APPLICATION
    _APPLICATION = QApplication.instance() or QApplication([])
    yield _APPLICATION


def _snapshot(
    queue: str = "normal",
    *,
    status: str = "ready",
    diagnostics: tuple[Diagnostic, ...] = (),
) -> ClusterSnapshot:
    return ClusterSnapshot(
        status=status,
        collected_at="2026-08-18T12:00:00Z",
        user="demo",
        selected_queue=queue,
        queues=(
            QueueInfo("normal", "Open:Active", True, 20, 4, 16),
            QueueInfo("batch", "Open:Active", True, 2, 0, 2),
        ),
        hosts=(
            HostInfo(
                "node-busy",
                "ok",
                True,
                max_slots=16,
                total_jobs=14,
                running_jobs=14,
                suspended_jobs=0,
                reserved_slots=0,
                load_1m=8.0,
                cpu_utilization=0.8,
                memory_available_bytes=4 * 1024**3,
                memory_total_bytes=32 * 1024**3,
                swap_available_bytes=2 * 1024**3,
            ),
            HostInfo(
                "node-idle",
                "ok",
                True,
                max_slots=16,
                total_jobs=2,
                running_jobs=2,
                suspended_jobs=0,
                reserved_slots=0,
                load_1m=1.0,
                cpu_utilization=0.2,
                memory_available_bytes=32 * 1024**3,
                memory_total_bytes=40 * 1024**3,
                swap_available_bytes=8 * 1024**3,
            ),
        ),
        jobs=(
            JobInfo(
                "250",
                "demo",
                "RUN",
                "normal",
                "node-idle",
                "node-idle:node-busy",
                2,
                "Aug 20 10:48",
                "Aug 20 10:49",
                75,
                "calibre run",
            ),
            JobInfo(
                "251",
                "demo",
                "PEND",
                "batch",
                "",
                "",
                1,
                "Aug 20 10:50",
                "-",
                0,
                "waiting check",
            ),
        ),
        diagnostics=diagnostics,
    )
