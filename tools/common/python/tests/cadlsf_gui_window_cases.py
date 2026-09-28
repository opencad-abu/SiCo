"""LSF GUI window behavior cases."""

from __future__ import annotations

from cadlsf_gui_fixtures import application as application, _snapshot
from pathlib import Path
import pytest
from PyQt5.QtCore import QObject, Qt
from PyQt5.QtGui import QImage, QPainter
from PyQt5.QtWidgets import QApplication, QHeaderView, QLabel, QSplitter
from cadlsf.gui.main_window import LsfLoadMonitorWindow
from cadlsf.gui.models import HostTableModel, HostFilterProxyModel, JobTableModel, JobFilterProxyModel
from cadlsf_gui_fakes import FakeController


def test_host_filter_and_progress_delegate_render_nonblank(
    application: QApplication,
) -> None:
    model = HostTableModel()
    model.set_rows(_snapshot().hosts)
    proxy = HostFilterProxyModel()
    proxy.setSourceModel(model)
    assert proxy.rowCount() == 2
    proxy.set_query("idle")
    assert proxy.rowCount() == 1
    assert proxy.data(proxy.index(0, 1)) == "node-idle"

    window = LsfLoadMonitorWindow(auto_start=False)
    window._apply_snapshot(_snapshot())
    window.resize(1000, 640)
    window.show()
    application.processEvents()
    # 家族 chrome：无边框自绘标题栏，左上角带站点/产品 logo。
    assert window.windowFlags() & Qt.FramelessWindowHint
    assert window.title_bar.title.text() == "SiCo::LSF Loading Monitor"
    assert window.title_bar.findChild(QLabel, "copilotTitleLogo") is not None
    image = QImage(window.size(), QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    window.render(painter)
    painter.end()
    assert image.width() == window.width()
    assert image.height() == 640
    sampled = {
        image.pixelColor(x, y).rgb()
        for x in range(0, image.width(), 25)
        for y in range(0, image.height(), 20)
    }
    assert len(sampled) > 8
    window.close()
    application.processEvents()


def test_job_model_filter_and_window_tab_layout(
    application: QApplication,
) -> None:
    model = JobTableModel()
    model.set_rows(_snapshot().jobs)
    assert model.data(model.index(0, 0)) == "250"
    assert model.data(model.index(0, 3)) == "node-idle"
    assert model.data(model.index(0, 3), Qt.ToolTipRole) == (
        "node-idle:node-busy"
    )
    assert model.data(model.index(0, 5)) == "00:01:15"

    proxy = JobFilterProxyModel()
    proxy.setSourceModel(model)
    proxy.set_status("PEND")
    assert proxy.rowCount() == 1
    assert proxy.data(proxy.index(0, 0)) == "251"
    proxy.set_query("calibre")
    assert proxy.rowCount() == 0
    proxy.set_status("All states")
    assert proxy.rowCount() == 1
    proxy.set_query("node-busy")
    assert proxy.rowCount() == 1

    window = LsfLoadMonitorWindow(auto_start=False)
    window._apply_snapshot(_snapshot())
    assert window.tabs.count() == 2
    assert window.tabs.tabText(0) == "Resources"
    assert window.tabs.tabText(1) == "Jobs"
    assert window.jobs.job_model.rowCount() == 2
    assert window.jobs.job_model.columnCount() == 10
    assert window.jobs.job_model.headerData(8, Qt.Horizontal) == "Job Name"
    assert window.jobs.job_model.headerData(9, Qt.Horizontal) == "Action"
    assert window.jobs.job_status_combo.findText("RUN") >= 0
    assert window.jobs.job_count_label.text() == "2 jobs for demo"
    header = window.resources.queue_table.horizontalHeader()
    for column in range(window.resources.queue_model.columnCount()):
        assert header.sectionResizeMode(column) == QHeaderView.Stretch
    window.resize(1000, 640)
    window.show()
    application.processEvents()
    assert window.width() == window.minimumWidth() == 1140
    assert window.jobs.job_action_delegate._icon_path == (
        Path(__file__).resolve().parents[4] / "share/sico/icons/actions/close-flow.png"
    )
    assert not window.jobs.job_action_delegate._kill_icon.isNull()
    window.tabs.setCurrentIndex(1)
    application.processEvents()
    action_rect = window.jobs.job_table.visualRect(window.jobs.job_proxy.index(0, 9))
    jobs_image = window.jobs.job_table.viewport().grab().toImage()
    assert any(
        jobs_image.pixelColor(x, y).red() > 180
        and jobs_image.pixelColor(x, y).green() < 140
        and jobs_image.pixelColor(x, y).blue() < 140
        for x in range(action_rect.left(), action_rect.right() + 1)
        for y in range(action_rect.top(), action_rect.bottom() + 1)
    )
    window.tabs.setCurrentIndex(0)
    application.processEvents()
    resources = window.tabs.widget(0)
    splitter = resources.findChild(QSplitter)
    assert splitter is not None
    host_panel = splitter.widget(1)
    assert window.resources.queue_combo.parent() is host_panel
    assert window.resources.search_edit.parent() is host_panel
    assert window.resources.available_only.parent() is host_panel
    assert window.resources.auto_refresh.parent() is host_panel
    assert window.resources.interval_spin.parent() is host_panel
    assert window.resources.refresh_button.parent() is host_panel
    assert window.resources.sample_label.parent() is host_panel
    queue_y = window.resources.queue_table.mapTo(window, window.resources.queue_table.rect().topLeft()).y()
    controls_y = window.resources.queue_combo.mapTo(window, window.resources.queue_combo.rect().topLeft()).y()
    assert controls_y > queue_y
    host_margins = host_panel.layout().contentsMargins()
    resource_margins = resources.layout().contentsMargins()
    assert host_margins.top() == 6
    assert resource_margins.bottom() == 6
    window.close()
    application.processEvents()


def test_window_refresh_pause_failure_and_close_lifecycle(
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LOGO", raising=False)
    monkeypatch.delenv("COMPANY", raising=False)
    controller = FakeController()
    window = LsfLoadMonitorWindow(
        controller=controller,
        initial_queue="normal",
        auto_start=False,
    )
    assert window.windowTitle() == "SiCo::LSF Loading Monitor"
    assert window.findChildren(type(window.resources.refresh_button), "")
    assert window.findChild(QObject, "queueCombo") is window.resources.queue_combo
    assert window.findChild(QObject, "hostTable") is window.resources.host_table
    assert window.findChild(QObject, "reloadTopologyButton") is (
        window.resources.reload_topology_button
    )
    assert window.findChild(QObject, "applyButton") is None

    window.refresh()
    assert controller.submissions == ["normal"]
    controller.busy = False
    window.reload_topology()
    assert controller.submissions == ["normal", ("normal", True)]
    controller.submissions.pop()
    window._auto_refresh_timeout()
    assert controller.submissions == ["normal"]
    controller.busy = False
    window._auto_refresh_timeout()
    assert controller.submissions == ["normal", "normal"]

    controller.busy = False
    window._apply_snapshot(_snapshot())
    assert window.resources.queue_combo.currentText() == "normal"
    assert window.resources.host_model.rowCount() == 2
    assert window.status.kind == "ready"
    controller.failed.emit("temporary failure")
    assert window.status.kind == "stale"
    assert window.status.diagnostic_label.text() == "temporary failure"

    window.resources.auto_refresh.setChecked(False)
    assert not window.refresh_timer.isActive()
    window.close()
    application.processEvents()
    assert controller.shutdown_calls == 1
