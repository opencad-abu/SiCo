"""gui process tab cases regressions."""

from __future__ import annotations
from dataclasses import replace
from pathlib import Path
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication,
    QTabBar,
)
from mtsnetlistor.gui.main_window import (  # noqa: E402
    MtsMainWindow,
)
from mtsnetlistor.gui.generation_coordinator import AcceptedGeneration


def test_process_tabs_use_new_process_placeholder_and_isolate_state(
    application: QApplication, tmp_path: Path
) -> None:
    window = MtsMainWindow(session=None)
    try:
        assert window.run_all_button.text() == "Run All"
        assert window.run_all_button.toolTip().endswith("in order")
        assert [window.process_tabs.tabText(i) for i in range(window.process_tabs.count())] == [
            "Process1",
            "New Process",
        ]
        first = window._pages[0]
        first.source_edit.setText(str(tmp_path / "process1.cds.lib"))

        window._tab_clicked(1)
        assert [window.process_tabs.tabText(i) for i in range(window.process_tabs.count())] == [
            "Process1",
            "Process2",
            "New Process",
        ]
        assert len(window._pages) == 2
        second = window._pages[1]
        assert first.controller is not second.controller
        assert first.source_edit.text().endswith("process1.cds.lib")
        assert second.source_edit.text() == ""

        window.process_tabs.setCurrentWidget(second)
        # Existing single-process integrations continue to address the active
        # process page through the host's compatibility proxy.
        assert window.source_edit is second.source_edit
        second.source_edit.setText(str(tmp_path / "process2.cds.lib"))
        window.process_tabs.setCurrentWidget(first)
        assert window.source_edit.text().endswith("process1.cds.lib")
        window.process_tabs.setCurrentWidget(second)
        assert window.source_edit.text().endswith("process2.cds.lib")
        tab_bar = window.process_tabs.tabBar()
        assert tab_bar.tabButton(0, QTabBar.RightSide) is not None
        assert tab_bar.tabButton(1, QTabBar.RightSide) is not None
        placeholder_index = window.process_tabs.count() - 1
        assert tab_bar.tabTextColor(placeholder_index).name() == "#2e8b57"
        assert tab_bar.tabButton(placeholder_index, QTabBar.RightSide) is None
        window.show()
        application.processEvents()
        assert window.run_all_button.y() > window.process_tabs.y()
        assert window.cancel_all_button.y() == window.run_all_button.y()
        assert window.run_all_button.x() < window.cancel_all_button.x()
        assert abs(window.load_config_button.y() - window.run_all_button.y()) <= 1
        assert abs(window.save_config_button.y() - window.run_all_button.y()) <= 1
        assert window.load_config_button.x() < window.save_config_button.x()
    finally:
        window.close()
        window.controller.close()


def test_process_tab_close_button_removes_page_and_renumbers(
    application: QApplication,
) -> None:
    window = MtsMainWindow(session=None)
    try:
        window._tab_clicked(1)
        window._tab_clicked(2)
        assert [window.process_tabs.tabText(i) for i in range(window.process_tabs.count())] == [
            "Process1",
            "Process2",
            "Process3",
            "New Process",
        ]
        removed = window._pages[1]
        window._close_process_tab(window.process_tabs.indexOf(removed))
        application.processEvents()
        assert removed not in window._pages
        assert [window.process_tabs.tabText(i) for i in range(window.process_tabs.count())] == [
            "Process1",
            "Process2",
            "New Process",
        ]

        # Deleting the final configured page leaves a usable Process1 plus
        # the insertion placeholder instead of an empty tab bar.
        window._close_process_tab(window.process_tabs.indexOf(window._pages[0]))
        window._close_process_tab(window.process_tabs.indexOf(window._pages[0]))
        application.processEvents()
        assert len(window._pages) == 1
        assert [window.process_tabs.tabText(i) for i in range(window.process_tabs.count())] == [
            "Process1",
            "New Process",
        ]
    finally:
        window.close()
        window.controller.close()


def test_host_session_replacement_updates_all_page_controllers(
    application: QApplication,
) -> None:
    window = MtsMainWindow(session=None)
    try:
        second = window._add_process_page()
        replacement = object()

        window.session = replacement

        assert window.session is replacement
        assert all(page.session is replacement for page in window._pages)
        assert all(page.controller.session is replacement for page in window._pages)
        assert second.controller.session is replacement
    finally:
        window.close()
        window.controller.close()


def test_run_all_processes_tabs_in_order(application: QApplication) -> None:
    window = MtsMainWindow(session=None)
    try:
        window._tab_clicked(1)
        first, second = window._pages
        first.source_edit.setText("/tmp/process1.cds.lib")
        second.source_edit.setText("/tmp/process2.cds.lib")
        calls = []
        first._run = lambda: calls.append(first) or True
        second._run = lambda: calls.append(second) or True

        window._run_all()
        window._poll_run_all()
        assert calls == [first]
        assert window._run_all_current is first
        assert not first.upper_workspace.isEnabled()
        assert not second.upper_workspace.isEnabled()
        assert first.log.isEnabled()
        assert second.log.isEnabled()
        assert not window.load_config_button.isEnabled()
        assert not window.save_config_button.isEnabled()

        first.generation.accepted = AcceptedGeneration(object(), None)
        first.controller._state = replace(
            first.controller.state, busy=False, stage="generation_ready", error=""
        )
        window._poll_run_all()
        application.processEvents()
        window._poll_run_all()
        assert calls == [first, second]
        assert window._run_all_current is second

        second.generation.accepted = AcceptedGeneration(object(), None)
        second.controller._state = replace(
            second.controller.state, busy=False, stage="generation_ready", error=""
        )
        window._poll_run_all()
        application.processEvents()
        assert not window._run_all_active
        assert window.run_all_button.isEnabled()
        assert not window.cancel_all_button.isEnabled()
        assert first.upper_workspace.isEnabled()
        assert second.upper_workspace.isEnabled()
        assert window.load_config_button.isEnabled()
        assert window.save_config_button.isEnabled()
        assert window.statusBar().currentMessage() == "Run All finished"
    finally:
        window.close()
        window.controller.close()


def test_cancel_all_cancels_every_page_in_batch_snapshot(
    application: QApplication,
) -> None:
    window = MtsMainWindow(session=None)
    try:
        window._tab_clicked(1)
        first, second = window._pages
        first.source_edit.setText("/tmp/process1.cds.lib")
        second.source_edit.setText("/tmp/process2.cds.lib")
        canceled = []
        first._cancel = lambda: canceled.append(first)
        second._cancel = lambda: canceled.append(second)
        # Simulate the initial wait state before any one page has become the
        # active generation page.
        first.controller._state = replace(first.controller.state, busy=True)
        window._run_all()
        assert window._run_all_current is None
        window._cancel_all()
        assert canceled == [first, second]
        assert not window._run_all_active
        assert first.upper_workspace.isEnabled()
        assert second.upper_workspace.isEnabled()
    finally:
        window.close()
        window.controller.close()
