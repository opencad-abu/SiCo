from __future__ import annotations

import os
from pathlib import Path
from threading import Event

import pytest


RUN_XCB_SMOKE = os.environ.get("CAD_LSF_RUN_XCB_SMOKE") == "1"


@pytest.mark.skipif(
    not RUN_XCB_SMOKE,
    reason="set CAD_LSF_RUN_XCB_SMOKE=1 and provide an X11 display",
)
def test_xcb_window_populates_from_background_worker(tmp_path: Path) -> None:
    from PyQt5.QtCore import QTimer
    from PyQt5.QtWidgets import QApplication

    from cadlsf.collector import CollectorConfig
    from cadlsf.gui.main_window import LsfLoadMonitorWindow
    from cadlsf.gui.workers import RefreshController
    from cadlsf.model import ClusterSnapshot, HostInfo, JobInfo, QueueInfo

    snapshot = ClusterSnapshot(
        status="ready",
        collected_at="2026-08-18T12:00:00Z",
        user="demo",
        selected_queue="normal",
        queues=(QueueInfo("normal", "Open:Active", True, 4, 1, 3),),
        hosts=(
            HostInfo(
                "node01",
                "ok",
                True,
                max_slots=16,
                total_jobs=3,
                running_jobs=3,
                suspended_jobs=0,
                reserved_slots=0,
                load_1m=0.4,
                cpu_utilization=0.18,
                memory_available_bytes=24 * 1024**3,
                memory_total_bytes=32 * 1024**3,
                swap_available_bytes=8 * 1024**3,
            ),
        ),
        jobs=(
            JobInfo(
                "250",
                "demo",
                "RUN",
                "normal",
                "node01",
                "node01",
                1,
                "Aug 20 10:48",
                "Aug 20 10:49",
                75,
                "calibre run",
            ),
        ),
    )

    class FakeCollector:
        def snapshot(
            self, _queue: str | None, *, include_jobs: bool = False
        ) -> ClusterSnapshot:
            return snapshot

    def factory(_config: CollectorConfig, _cancel_event: Event) -> FakeCollector:
        return FakeCollector()

    application = QApplication.instance() or QApplication([])
    assert QApplication.platformName() == "xcb"
    controller = RefreshController(
        CollectorConfig(), collector_factory=factory
    )
    window = LsfLoadMonitorWindow(
        controller=controller,
        initial_queue="normal",
        output=tmp_path / "selection.tsv",
    )
    completed: list[tuple[bool, bool, bool, bool, bool]] = []

    def poll() -> None:
        if window.resources.host_model.rowCount() == 1 and window.jobs.job_model.rowCount() == 1:
            window.tabs.setCurrentIndex(1)
            application.processEvents()
            image = window.grab().toImage()
            colors = {
                image.pixelColor(x, y).rgb()
                for x in range(0, image.width(), 40)
                for y in range(0, image.height(), 30)
            }
            completed.append(
                (
                    window.isVisible(),
                    window.selector.use_auto_button.isEnabled(),
                    window.selector.use_host_button.isEnabled(),
                    window.tabs.currentWidget() is window.jobs.job_table.parent(),
                    image.width() == window.width()
                    and image.height() == window.height()
                    and len(colors) > 1,
                )
            )
            window.close()
            application.quit()

    poll_timer = QTimer()
    poll_timer.timeout.connect(poll)
    poll_timer.start(20)
    timeout_timer = QTimer()
    timeout_timer.setSingleShot(True)
    timeout_timer.timeout.connect(window.close)
    timeout_timer.timeout.connect(application.quit)
    timeout_timer.start(3_000)
    window.show()
    application.exec_()

    assert completed == [(True, True, True, True, True)]
