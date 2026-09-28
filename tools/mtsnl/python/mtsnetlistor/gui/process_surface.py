"""Compose the source browser, cell settings and resizable process log."""

from __future__ import annotations
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSplitter,
    QSizePolicy,
    QTableWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from cadgui.library_browser import LibraryBrowserWidget
from .corner_editor import CornerEditor


class ProcessSurface(QWidget):
    def __init__(self, project_names, simulator_form, publication_form, parent=None):
        super().__init__(parent)
        root = self
        outer = QVBoxLayout(root)

        # Keep the complete work surface together so the outer vertical
        # splitter can resize it against the logger as one coherent pane.
        self.upper_workspace = QWidget(root)
        # The nested work panes have useful size hints, but they must not
        # prevent the outer handle from allocating more height to the logger.
        self.upper_workspace.setSizePolicy(
            QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        )
        workspace_layout = QVBoxLayout(self.upper_workspace)
        workspace_layout.setContentsMargins(0, 0, 0, 0)

        project_row = QHBoxLayout()
        self.project_prompt = QLabel("Project:", root)
        self.project_combo = QComboBox(root)
        self.project_combo.setObjectName("sourceProject")
        self.project_combo.addItem("Manual / Current Environment", "")
        for project_name in project_names:
            self.project_combo.addItem(project_name, project_name)
        self.project_combo.setToolTip(
            "Load a source project module in isolated workers; the current "
            "Virtuoso environment is not changed"
        )
        project_row.addWidget(self.project_prompt)
        project_row.addWidget(self.project_combo, 1)
        workspace_layout.addLayout(project_row)

        source_row = QHBoxLayout()
        self.source_prompt = QLabel("Library Definition File(cds.lib):", root)
        self.source_edit = QLineEdit()
        self.source_edit.setPlaceholderText("Library Definition File(cds.lib):")
        self.browse_source = QPushButton("Browse…")
        self.refresh_source = QPushButton("Refresh")
        source_row.addWidget(self.source_prompt)
        source_row.addWidget(self.source_edit, 1)
        source_row.addWidget(self.browse_source)
        source_row.addWidget(self.refresh_source)
        workspace_layout.addLayout(source_row)
        # Keep the old fields as hidden compatibility state for request
        # assembly and external scripts; the visible selector is the three
        # list widgets below.
        self.source_library = QLineEdit(root)
        self.source_library.setReadOnly(True)
        self.source_library.hide()
        self.source_cell = QLineEdit(root)
        self.source_cell.setReadOnly(True)
        self.source_cell.hide()
        self.source_view = QLineEdit(root)
        self.source_view.setReadOnly(True)
        self.source_view.hide()

        source_cells_box = QGroupBox("Source Cells")
        self.source_cells_box = source_cells_box
        source_cells_layout = QVBoxLayout(source_cells_box)
        self.source_cells_list = QListWidget(source_cells_box)
        self.source_cells_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.source_cells_list.setContextMenuPolicy(Qt.CustomContextMenu)
        source_cells_layout.addWidget(self.source_cells_list)

        source_box = QGroupBox("Source Library Browser")
        self.source_browser_box = source_box
        source_layout = QVBoxLayout(source_box)
        self.library_browser = LibraryBrowserWidget(
            parent=source_box,
            content_top_margin=20,
        )
        # The MTS adapter owns the context menu and its meaning.  The shared
        # widget only presents catalog data and emits viewActivated.
        self.library_browser.view_list.setContextMenuPolicy(Qt.CustomContextMenu)
        source_layout.addWidget(self.library_browser, 1)

        # Compatibility aliases keep the established MTS integration surface
        # stable while the actual browser implementation lives in common.
        self.source_lists_splitter = self.library_browser.splitter
        self.source_library_list = self.library_browser.library_list
        self.source_cell_list = self.library_browser.cell_list
        self.source_view_list = self.library_browser.view_list
        self.source_library_filter = self.library_browser.library_filter
        self.source_cell_filter = self.library_browser.cell_filter
        self.source_view_filter = self.library_browser.view_filter
        self.library_list = self.source_library_list
        self.cell_list = self.source_cell_list
        self.view_list = self.source_view_list

        self.source_workspace_splitter = QSplitter(Qt.Vertical)
        self.source_workspace_splitter.setChildrenCollapsible(False)
        # Keep the browser above the selected-cell queue.  The browser is the
        # discovery surface for lib/cell/view selection; the queue remains
        # below it for reviewing the views already selected for processing.
        self.source_workspace_splitter.addWidget(source_box)
        self.source_workspace_splitter.addWidget(source_cells_box)
        self.source_workspace_splitter.setStretchFactor(0, 3)
        self.source_workspace_splitter.setStretchFactor(1, 1)
        self.source_workspace_splitter.setSizes([520, 200])

        self.models_table = QTableWidget(0, 4)
        self.models_table.setHorizontalHeaderLabels(["Enabled", "Model File", "Corner", "Label"])
        self.models_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        model_header = self.models_table.horizontalHeader()
        model_header.setSectionResizeMode(QHeaderView.Interactive)
        model_header.setStretchLastSection(True)
        self.models_table.setColumnWidth(0, 80)
        self.models_table.setColumnWidth(1, 440)
        self.models_table.setColumnWidth(2, 120)
        self.models_table.setColumnWidth(3, 130)
        model_box = QGroupBox("Model Library Setup")
        model_layout = QVBoxLayout(model_box)
        model_buttons = QHBoxLayout()
        self.add_model = QPushButton("Add")
        self.remove_model = QPushButton("Remove")
        self.move_model_up = QPushButton("Move Up")
        self.move_model_down = QPushButton("Move Down")
        self.duplicate_model = QPushButton("Duplicate")
        model_buttons.addWidget(self.add_model)
        model_buttons.addWidget(self.remove_model)
        model_buttons.addWidget(self.move_model_up)
        model_buttons.addWidget(self.move_model_down)
        model_buttons.addWidget(self.duplicate_model)
        model_buttons.addStretch(1)
        model_layout.addWidget(self.models_table)
        # Keep the model list as the primary, stretchable surface and place
        # its row actions beneath it, matching the Advanced Options table in
        # Simulator Options.  This also keeps Add/Remove reachable when the
        # model pane is vertically resized.
        model_layout.addLayout(model_buttons)
        self.corner_editor = CornerEditor(self)
        model_layout.addWidget(self.corner_editor)

        # Right-side settings belong to the active Source Cells item.  Each
        # switch saves the current widgets before loading the next cell.
        self.cell_settings_frame = QGroupBox("No Source Cell Selected")
        cell_settings_layout = QVBoxLayout(self.cell_settings_frame)
        self.workspace_splitter = QSplitter(Qt.Vertical, self.cell_settings_frame)
        self.workspace_splitter.setChildrenCollapsible(False)
        self.workspace_splitter.addWidget(simulator_form)
        self.workspace_splitter.addWidget(model_box)
        self.workspace_splitter.addWidget(publication_form)
        self.workspace_splitter.setStretchFactor(0, 3)
        self.workspace_splitter.setStretchFactor(1, 3)
        self.workspace_splitter.setStretchFactor(2, 1)
        self.workspace_splitter.setSizes([330, 300, 160])
        cell_settings_layout.addWidget(self.workspace_splitter)

        self.main_splitter = QSplitter(Qt.Horizontal)
        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.addWidget(self.source_workspace_splitter)
        self.main_splitter.addWidget(self.cell_settings_frame)
        self.main_splitter.setStretchFactor(0, 3)
        self.main_splitter.setStretchFactor(1, 4)
        self.main_splitter.setSizes([590, 790])
        workspace_layout.addWidget(self.main_splitter, 1)

        self.open_result_button = QPushButton("Open Result")
        self.open_result_button.setObjectName("openResultButton")
        self.open_result_button.setProperty("bottomAction", True)
        self.open_result_button.setEnabled(False)
        self.run_button = QPushButton("Run Current")
        self.run_button.setObjectName("runButton")
        self.run_button.setProperty("bottomAction", True)
        # Retain a hidden compatibility handle for integrations that used the
        # former separate publication button.  Publication is now only
        # reachable through Run and this widget is never added to the layout.
        self.publish_button = QPushButton("Publish Views", root)
        self.publish_button.hide()
        self.publish_button.setEnabled(False)
        self.cancel_button = QPushButton("Cancel Current")
        self.cancel_button.setObjectName("cancelButton")
        self.cancel_button.setProperty("dialogDanger", True)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(70)
        self.log.document().setMaximumBlockCount(5000)

        self.log_splitter = QSplitter(Qt.Vertical, root)
        self.log_splitter.setChildrenCollapsible(False)
        self.log_splitter.addWidget(self.upper_workspace)
        self.log_splitter.addWidget(self.log)
        self.log_splitter.setStretchFactor(0, 1)
        self.log_splitter.setStretchFactor(1, 0)
        self.log_splitter.setSizes([700, 150])
        outer.addWidget(self.log_splitter, 1)

        # Execution controls belong to the bottom edge of this process tab,
        # below the resizable logger. Configuration loading remains above the
        # work surface so it does not compete visually with Run/Cancel.
        self.execution_row = QHBoxLayout()
        self.execution_row.addWidget(self.open_result_button)
        self.execution_row.addStretch(1)
        self.execution_row.addWidget(self.run_button)
        self.execution_row.addWidget(self.cancel_button)
        outer.addLayout(self.execution_row)

