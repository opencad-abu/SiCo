"""Lifecycle boundaries of the composed process host and independent views."""

from dataclasses import replace

import pytest

from gui_window_fixtures import application
from mtsnetlistor.gui.controller import MtsController
from mtsnetlistor.gui.main_window import MtsMainWindow, ProcessPage
from mtsnetlistor.gui.process_page import ProcessPage as OwnedProcessPage

__all__ = ["application"]


def test_repeated_run_all_keeps_snapshot_and_blocks_tab_mutation(application):
    window = MtsMainWindow(session=None)
    try:
        first = window._pages[0]
        first.source_edit.setText("/tmp/first.cds.lib")
        first.controller._state = replace(first.controller.state, busy=True)
        window._run_all()
        snapshot = window._batch._snapshot
        window._run_all()
        assert window._batch._snapshot is snapshot
        assert window._run_all_current is None
        assert window._run_all_timer.interval() == 100
        window.process_tabs.setCurrentIndex(window._new_process_tab_index())
        window._tab_clicked(window._new_process_tab_index())
        assert window.process_tabs.currentWidget() is first
        window._close_process_tab(0)
        assert window._pages == [first]
    finally:
        window.close()


def test_run_all_observes_state_published_synchronously_by_run(application):
    window = MtsMainWindow(session=None)
    try:
        first = window._pages[0]
        first.source_edit.setText("/tmp/first.cds.lib")

        def run():
            first.controller._state = replace(first.controller.state, stage="error", error="failed in submit")
            return True

        first._run = run
        window._run_all()
        window._poll_run_all()
        application.processEvents()
        assert not window._run_all_active
        assert window.statusBar().currentMessage() == "Run All finished with errors: Process1"
    finally:
        window.close()


def test_candidate_session_failure_shuts_down_unadopted_page(application, monkeypatch):
    window = MtsMainWindow(session=None)
    original = tuple(window._pages)
    stopped = []
    shutdown = ProcessPage.shutdown

    def record_shutdown(page):
        stopped.append(page)
        shutdown(page)

    def fail_session(*args):
        raise RuntimeError("session rejected")

    monkeypatch.setattr(ProcessPage, "shutdown", record_shutdown)
    monkeypatch.setattr(MtsController, "set_session", fail_session)
    try:
        with pytest.raises(RuntimeError, match="session rejected"):
            window._allocate_workspace_page()
        assert tuple(window._pages) == original
        assert len(stopped) == 1 and stopped[0] not in original
        assert not stopped[0]._poll_timer.isActive()
    finally:
        window.close()


def test_compatibility_views_reference_their_only_owner(application):
    window = MtsMainWindow(session=None)
    try:
        page = window._pages[0]
        assert ProcessPage is OwnedProcessPage
        assert page.models_table is page._model_editor.table is page._surface.models_table
        assert page.options_table is page._option_editor.table is page._simulator_form.options_table
        assert page.target_library is page._publication_form.target_library
        assert page._result_rows is page._results.rows
        for name in ("models_table", "target_library", "_result_rows", "_active_cell_key", "_active_dialect"):
            assert name not in vars(page)
        page._active_cell_key = ("lib", "cell", "schematic")
        assert page._selection.active == ("lib", "cell", "schematic")
    finally:
        window.close()


def test_surface_table_buttons_dispatch_to_composed_owners(application):
    window = MtsMainWindow(session=None)
    try:
        page = window._pages[0]
        page._surface.add_model.click()
        page._surface.duplicate_model.click()
        assert page.models_table.rowCount() == 2
        page._surface.remove_model.click()
        assert page.models_table.rowCount() == 1
        page._simulator_form.add_option.click()
        page._simulator_form.duplicate_option.click()
        assert page.options_table.rowCount() == 2
        page._simulator_form.remove_option.click()
        assert page.options_table.rowCount() == 1
    finally:
        window.close()
