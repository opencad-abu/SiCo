"""gui layout cases regressions."""

from __future__ import annotations
from PyQt5.QtCore import QPoint, Qt  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication,
    QFormLayout,
    QHeaderView,
    QListWidget,
    QSizePolicy,
    QTreeWidget,
)
from cadgui.chrome import apply_family_style, WINDOW_STYLESHEET  # noqa: E402
from mtsnetlistor.gui.main_window import (  # noqa: E402
    MtsMainWindow,
)
from gui_window_fixtures import _draft, _append_source_cell


def test_source_lists_and_resizable_splitters(application: QApplication) -> None:
    window = MtsMainWindow(session=None)
    try:
        assert window.source_prompt.text() == "Library Definition File(cds.lib):"
        assert window.source_edit.placeholderText() == "Library Definition File(cds.lib):"
        assert isinstance(window.source_library_list, QTreeWidget)
        assert all(
            isinstance(widget, QListWidget)
            for widget in (
                window.source_cell_list,
                window.source_view_list,
            )
        )
        assert window.source_lists_splitter.orientation() == Qt.Horizontal
        assert window.source_lists_splitter.count() == 3
        assert window.source_lists_splitter.childrenCollapsible() is False
        assert window.workspace_splitter.orientation() == Qt.Vertical
        assert window.workspace_splitter.count() == 3
        assert window.workspace_splitter.childrenCollapsible() is False
        assert window.log_splitter.orientation() == Qt.Vertical
        assert window.log_splitter.count() == 2
        assert window.log_splitter.childrenCollapsible() is False
        assert window.log_splitter.widget(0) is window.upper_workspace
        assert window.log_splitter.widget(1) is window.log
        assert window.log.maximumHeight() > 150
        assert window.upper_workspace.sizePolicy().verticalPolicy() == QSizePolicy.Ignored
        # The browser stays above the selected-cell queue. Assert widget
        # identity as well as titles so a coincidental title change cannot
        # mask an accidental reordering.
        assert window.source_workspace_splitter.widget(0) is window.source_browser_box
        assert window.source_workspace_splitter.widget(1) is window.source_cells_box
        assert window.source_workspace_splitter.widget(0).title() == "Source Library Browser"
        assert window.source_workspace_splitter.widget(1).title() == "Source Cells"
        assert window.open_result_button.text() == "Open Result"
        assert window.open_result_button.objectName() == "openResultButton"
        assert window.open_result_button.property("bottomAction") is True
        assert not window.open_result_button.isEnabled()
        assert window.run_button.text() == "Run Current"
        assert window.run_button.objectName() == "runButton"
        assert window.run_button.property("bottomAction") is True
        assert window.cancel_button.objectName() == "cancelButton"
        assert window.cancel_button.text() == "Cancel Current"
        assert window.cancel_button.property("dialogDanger") is True
        assert not window.publish_button.isVisible()
        window.show()
        application.processEvents()
        assert window.open_result_button.y() == window.run_button.y()
        assert window.open_result_button.x() < window.run_button.x()
        assert window.run_button.y() > window.log.y()
        assert window.cancel_button.y() == window.run_button.y()
        assert window.run_button.x() < window.cancel_button.x()
    finally:
        window.close()
        window.controller.close()


def test_selected_cell_titles_the_right_side_settings_frame(
    application: QApplication,
) -> None:
    window = MtsMainWindow(session=None)
    try:
        assert window.cell_settings_frame.title() == "No Source Cell Selected"
        first = _draft("source", "first", "schematic", temp="27")
        second = _draft("source", "second", "layout", temp="85")
        first_item = _append_source_cell(window, first)
        second_item = _append_source_cell(window, second)

        window.source_cells_list.setCurrentItem(first_item)
        assert first_item.text() == "source/first/schematic"
        assert window.cell_settings_frame.title() == "source/first/schematic"
        assert window.cell_settings_frame.toolTip() == "source/first/schematic"
        window.source_cells_list.setCurrentItem(second_item)
        assert second_item.text() == "source/second/layout"
        assert window.cell_settings_frame.title() == "source/second/layout"
        assert window.cell_settings_frame.toolTip() == "source/second/layout"

        window._delete_source_cell(second_item)
        assert window.cell_settings_frame.title() == "source/first/schematic"
        window._delete_source_cell(first_item)
        assert window.cell_settings_frame.title() == "No Source Cell Selected"
    finally:
        window.close()
        window.controller.close()


def test_logger_splitter_can_allocate_height_in_both_directions(
    application: QApplication,
) -> None:
    window = MtsMainWindow(session=None)
    try:
        window.show()
        application.processEvents()
        initial = window.log_splitter.sizes()
        assert len(initial) == 2
        # Moving the handle upward should increase the logger pane instead of
        # being clamped by the nested work panes' preferred height.
        window.log_splitter.moveSplitter(max(1, initial[0] - 80), 1)
        application.processEvents()
        resized = window.log_splitter.sizes()
        assert resized[1] > initial[1]
        assert resized[0] < initial[0]

        # Moving the handle back down restores work-area height and shrinks
        # the logger, confirming that the handle works in both directions.
        window.log_splitter.moveSplitter(initial[0] + 40, 1)
        application.processEvents()
        restored = window.log_splitter.sizes()
        assert restored[0] > resized[0]
        assert restored[1] < resized[1]
    finally:
        window.close()
        window.controller.close()


def test_process_tabs_host_plain_pages_without_their_own_title_bar(
    application: QApplication,
) -> None:
    """进程页是标签页里的普通页面：只有主窗口带家族无边框标题栏。"""

    from cadgui.titlebar import SiTitleBar

    window = MtsMainWindow(session=None)
    try:
        page = window._active_page()
        assert page is not None
        assert not hasattr(page, "title_bar")
        assert not page.isWindow()
        assert window.windowFlags() & Qt.FramelessWindowHint
        assert window.title_bar.title.text() == "SiCo::MTS Netlistor"
        # 窗口里只有一条标题栏：标签页不再各自画 Silicon Copilot 抬头和红线。
        assert len(window.findChildren(SiTitleBar)) == 1
    finally:
        window.close()


def test_group_frames_commands_and_overwrite_columns_align(
    application: QApplication,
) -> None:
    previous_style = application.styleSheet()
    application.setStyle("Fusion")
    apply_family_style(application)
    assert application.styleSheet() == WINDOW_STYLESHEET
    window = MtsMainWindow(session=None)
    try:
        window.show()
        application.processEvents()

        # Workspace configuration commands occupy the host's bottom-left;
        # per-process execution remains grouped at the bottom of the tab.
        assert window.load_config_button.x() < window.save_config_button.x()
        assert window.run_button.x() < window.cancel_button.x()

        # Both rows reserve the same Generate-column width.
        assert window.publish_symbol.width() == window.publish_text.width()
        assert (
            window.overwrite_symbol_view.mapTo(window, QPoint(0, 0)).x()
            == window.overwrite_netlist_view.mapTo(window, QPoint(0, 0)).x()
        )

        # Explicit group borders plus title margin keep the first controls
        # below the title/frame area in Fusion instead of crossing the top.
        assert window.source_lists_splitter.y() >= 20
        assert window.source_cells_list.y() >= 20
        assert window.simulator.y() >= 20
        assert window.models_table.parentWidget().layout().itemAt(0).geometry().top() >= 20
    finally:
        window.close()
        window.controller.close()
        application.setStyleSheet(previous_style)


def test_simulator_form_only_exposes_requested_process_controls(
    application: QApplication,
) -> None:
    window = MtsMainWindow(session=None)
    try:
        labels = []
        simulator_box = window.simulator.parentWidget()
        while simulator_box is not None and simulator_box.title() != "Simulator Options":
            simulator_box = simulator_box.parentWidget()
        assert simulator_box is not None
        form = simulator_box.layout()
        assert isinstance(form, QFormLayout)
        for row in range(form.rowCount()):
            label = form.itemAt(row, QFormLayout.LabelRole)
            if label is not None:
                labels.append(label.widget().text())
        assert labels == ["Simulator", "Temperature", "Scale", "Gmin", "Advanced Options"]
    finally:
        window.close()
        window.controller.close()


def test_multicell_layout_and_model_file_double_click_contract(
    application: QApplication,
) -> None:
    window = MtsMainWindow(session=None)
    try:
        assert window.main_splitter.orientation() == Qt.Horizontal
        assert window.source_workspace_splitter.orientation() == Qt.Vertical
        assert window.source_cells_list.contextMenuPolicy() == Qt.CustomContextMenu
        assert window.source_view_list.contextMenuPolicy() == Qt.CustomContextMenu
        header = window.models_table.horizontalHeader()
        assert all(
            header.sectionResizeMode(column) == QHeaderView.Interactive
            for column in range(window.models_table.columnCount())
        )
        assert window.models_table.horizontalHeaderItem(1).text() == "Model File"
        model_layout = window.models_table.parentWidget().layout()
        assert model_layout.indexOf(window.models_table) == 0
        assert model_layout.itemAt(1).layout() is not None
    finally:
        window.close()
        window.controller.close()
