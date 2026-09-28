"""LSF GUI jobs behavior cases."""

from __future__ import annotations

from cadlsf_gui_fixtures import application as application, _snapshot
import pytest
from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication
from cadlsf.gui.main_window import LsfLoadMonitorWindow
from cadlsf.gui.models import JOB_ID_ROLE
from cadlsf.model import Diagnostic
from cadlsf_gui_fakes import FakeConfirm, FakeController, FakeJobActionController


def test_job_kill_button_maps_proxy_row_confirms_and_refreshes(
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("cadlsf.gui.job_confirmation.SiConfirm", FakeConfirm)
    refresh = FakeController()
    actions = FakeJobActionController()
    window = LsfLoadMonitorWindow(
        controller=refresh,
        job_action_controller=actions,
        auto_start=False,
    )
    window._apply_snapshot(_snapshot())
    window.tabs.setCurrentIndex(1)
    window.jobs.job_search_edit.setText("node-busy")
    window.resize(1180, 720)
    window.show()
    application.processEvents()
    assert window.jobs.job_proxy.rowCount() == 1
    action_index = window.jobs.job_proxy.index(0, 9)
    assert action_index.data(JOB_ID_ROLE) == "250"
    position = window.jobs.job_table.visualRect(action_index).center()

    FakeConfirm.accept_kill = False
    QTest.mouseClick(window.jobs.job_table.viewport(), Qt.LeftButton, pos=position)
    assert actions.kill_calls == []

    FakeConfirm.accept_kill = True
    QTest.mouseClick(window.jobs.job_table.viewport(), Qt.LeftButton, pos=position)
    assert actions.kill_calls == ["250"]
    assert not window.jobs.job_action_delegate._enabled
    actions.busy = False
    actions.busyChanged.emit(False)
    actions.succeeded.emit("250")
    assert refresh.submissions == ["normal"]

    window.close()
    application.processEvents()
    assert actions.shutdown_calls == 1


def test_job_kill_failure_preserves_snapshot(
    application: QApplication,
) -> None:
    refresh = FakeController()
    actions = FakeJobActionController()
    window = LsfLoadMonitorWindow(
        controller=refresh,
        job_action_controller=actions,
        auto_start=False,
    )
    window._apply_snapshot(_snapshot())

    actions.failed.emit("251", "CollectorError: bkill failed: not found")

    assert window.status.kind == "error"
    assert window.status.state_label.text() == "Cannot kill job 251"
    assert "not found" in window.status.diagnostic_label.text()
    assert window.jobs.job_model.rowCount() == 2
    window.close()
    application.processEvents()


def test_failed_job_refresh_preserves_rows_and_unavailable_status(
    application: QApplication,
) -> None:
    controller = FakeController()
    window = LsfLoadMonitorWindow(
        controller=controller,
        auto_start=False,
    )
    window._apply_snapshot(_snapshot())
    failed_jobs = _snapshot(
        status="partial",
        diagnostics=(
            Diagnostic("job_collection_failed", "bjobs timed out"),
        ),
    )
    window._apply_snapshot(failed_jobs)
    assert window.jobs.job_model.rowCount() == 2
    assert "refresh unavailable" in window.jobs.job_count_label.text()
    assert window.jobs.job_count_label.toolTip() == "bjobs timed out"
    window.jobs.job_search_edit.setText("calibre")
    assert "refresh unavailable" in window.jobs.job_count_label.text()
    window.jobs.job_status_combo.setCurrentText("RUN")
    assert "refresh unavailable" in window.jobs.job_count_label.text()
    window.close()
    application.processEvents()
