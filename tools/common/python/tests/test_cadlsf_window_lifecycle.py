"""Snapshot application, queue fallback, and window close contracts."""

from dataclasses import replace

from cadlsf_gui_fixtures import application as application, _snapshot
from cadlsf_gui_fakes import (
    FakeController,
    FakeJobActionController,
    FakeValidationController,
)
from cadlsf.gui.main_window import LsfLoadMonitorWindow
from cadlsf.model import Diagnostic


def test_empty_snapshot_clears_startup_preference_and_recovers_queue(application):
    controller = FakeController()
    window = LsfLoadMonitorWindow(
        controller=controller,
        initial_queue="batch",
        auto_start=False,
    )
    try:
        window.refresh()
        assert controller.submissions == ["batch"]
        controller.submissions.clear()
        controller.busy = False
        empty = replace(_snapshot(), selected_queue=None, queues=(), hosts=(), jobs=())
        window._apply_snapshot(empty)
        window.refresh()
        assert controller.submissions == [None]
        controller.busy = False
        window._apply_snapshot(replace(_snapshot(), selected_queue=None))
        application.processEvents()
        assert controller.submissions == [None, "normal"]
        controller.busy = False
        window.resources.queue_combo.setCurrentText("batch")
        assert controller.submissions == [None, "normal", "batch"]
    finally:
        window.close()


def test_cached_snapshot_is_visible_but_selection_waits_for_final(
    application, tmp_path
):
    window = LsfLoadMonitorWindow(
        controller=FakeController(),
        output=tmp_path / "selected.tsv",
        validation_controller=FakeValidationController(),
        auto_start=False,
    )
    try:
        window._apply_cached_snapshot(_snapshot())
        assert window.resources.host_model.rowCount() == 2
        assert window.status.kind == "refreshing"
        assert not window.selector.use_auto_button.isEnabled()
        window._apply_snapshot(_snapshot())
        assert window.selector.use_auto_button.isEnabled()
    finally:
        window.close()


def test_job_failure_retains_old_rows_until_successful_refresh(application):
    window = LsfLoadMonitorWindow(controller=FakeController(), auto_start=False)
    try:
        window._apply_snapshot(_snapshot())
        original = window.jobs.job_model.rows
        failed = replace(
            _snapshot(),
            jobs=(),
            status="partial",
            diagnostics=(Diagnostic("job_collection_failed", "jobs unavailable"),),
        )
        window._apply_snapshot(failed)
        window.jobs.job_search_edit.setText("calibre")
        assert window.jobs.job_model.rows == original
        assert window.jobs.job_count_label.toolTip() == "jobs unavailable"
        assert not window.jobs.job_action_delegate._enabled
        window._apply_snapshot(replace(_snapshot(), jobs=()))
        assert window.jobs.job_model.rowCount() == 0
        assert window.jobs.job_count_label.toolTip() == ""
    finally:
        window.close()


def test_close_stops_updates_and_shutdowns_all_controllers(application, tmp_path):
    refresh = FakeController()
    actions = FakeJobActionController()
    validation = FakeValidationController(auto_complete=False)
    window = LsfLoadMonitorWindow(
        controller=refresh,
        job_action_controller=actions,
        validation_controller=validation,
        output=tmp_path / "selected.tsv",
        auto_start=False,
    )
    window._apply_snapshot(_snapshot())
    window.selector.use_auto()
    window.close()
    application.processEvents()
    displayed = window.status.state_label.text()
    window._apply_snapshot(replace(_snapshot(), hosts=()))
    refresh.failed.emit("late failure")
    actions.failed.emit("250", "late failure")
    validation.complete()
    assert window.resources.host_model.rowCount() == 2
    assert window.status.state_label.text() == displayed
    assert not window.refresh_timer.isActive()
    assert not window.jobs.job_action_delegate._enabled
    assert not (tmp_path / "selected.tsv").exists()
    assert (
        refresh.shutdown_calls,
        actions.shutdown_calls,
        validation.shutdown_calls,
    ) == (1, 1, 1)
