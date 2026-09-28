"""Small reusable widgets for the DSPF analysis workspace."""

from __future__ import annotations

import json
from typing import Any

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QStyle,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class PagedTablePane(QWidget):
    def __init__(self, model, *, sortable: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.model = model
        self.table = QTableView(self)
        self.table.setModel(model)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setSelectionMode(QTableView.SingleSelection)
        self.table.setSortingEnabled(sortable)
        self.table.verticalHeader().setDefaultSectionSize(24)
        self.table.horizontalHeader().setStretchLastSection(True)

        self.previous = QToolButton(self)
        self.previous.setIcon(self.style().standardIcon(QStyle.SP_ArrowBack))
        self.previous.setToolTip("Previous page")
        self.previous.setFixedSize(28, 28)
        self.next = QToolButton(self)
        self.next.setIcon(self.style().standardIcon(QStyle.SP_ArrowForward))
        self.next.setToolTip("Next page")
        self.next.setFixedSize(28, 28)
        self.page_label = QLabel("Page 1 / 1 (0 rows)", self)

        controls = QHBoxLayout()
        controls.addWidget(self.previous)
        controls.addWidget(self.page_label)
        controls.addStretch(1)
        controls.addWidget(self.next)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self.table, 1)
        layout.addLayout(controls)

        self.previous.clicked.connect(lambda: model.set_page(model.page - 1))
        self.next.clicked.connect(lambda: model.set_page(model.page + 1))
        model.pageChanged.connect(self._on_page_changed)
        self._on_page_changed(1, 1, 0)

    def _on_page_changed(self, page: int, pages: int, total: int) -> None:
        self.page_label.setText(f"Page {page} / {pages} ({total:,} rows)")
        self.previous.setEnabled(page > 1)
        self.next.setEnabled(page < pages)


class SummaryPane(QWidget):
    _FIELDS = (
        ("name", "Net"),
        ("node_count", "Nodes"),
        ("resistor_count", "Resistors"),
        ("ground_capacitor_count", "Ground capacitors"),
        ("coupling_capacitor_count", "Coupling capacitors"),
        ("total_resistance", "Total resistance"),
        ("max_resistance", "Maximum resistance"),
        ("ground_capacitance", "Ground capacitance"),
        ("coupling_capacitance", "Coupling capacitance"),
        ("max_capacitance", "Maximum capacitance"),
        ("computed_capacitance", "Computed capacitance"),
        ("declared_capacitance", "Declared capacitance"),
        ("capacitance_difference", "Capacitance difference"),
        ("dangling_node_count", "Dangling nodes"),
        ("connected_components", "Connected components"),
        ("diagnostic_count", "Diagnostics"),
    )

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.labels: dict[str, QLabel] = {}
        form = QFormLayout(self)
        form.setContentsMargins(12, 12, 12, 12)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        for key, title in self._FIELDS:
            label = QLabel("-", self)
            if key == "connected_components":
                label.setWordWrap(True)
            label.setTextInteractionFlags(label.textInteractionFlags())
            form.addRow(f"{title}:", label)
            self.labels[key] = label

    def set_summary(self, summary: dict[str, Any] | None) -> None:
        values = summary or {}
        for key, label in self.labels.items():
            value = values.get(key)
            if key == "connected_components" and value is None:
                value = values.get("connected_components_message")
            if isinstance(value, float):
                text = f"{value:.6g}"
            else:
                text = "-" if value is None else str(value)
            label.setText(text)


class PathPane(QWidget):
    requested = pyqtSignal(str, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.start_node = QLineEdit(self)
        self.end_node = QLineEdit(self)
        self.run_button = QPushButton("Analyze Path", self)
        self.busy = QProgressBar(self)
        self.busy.setRange(0, 0)
        self.busy.setFixedWidth(120)
        self.busy.setTextVisible(False)
        self.busy.hide()
        self.output = QPlainTextEdit(self)
        self.output.setReadOnly(True)
        self.output.setPlaceholderText("Select a net, then enter start and end nodes.")

        inputs = QFormLayout()
        inputs.addRow("Start node:", self.start_node)
        inputs.addRow("End node:", self.end_node)
        actions = QHBoxLayout()
        actions.addWidget(self.busy)
        actions.addStretch(1)
        actions.addWidget(self.run_button)
        layout = QVBoxLayout(self)
        layout.addLayout(inputs)
        layout.addLayout(actions)
        layout.addWidget(self.output, 1)
        self.run_button.clicked.connect(self._request)

    def _request(self) -> None:
        self.requested.emit(self.start_node.text().strip(), self.end_node.text().strip())

    def set_result(self, result: Any) -> None:
        if hasattr(result, "to_dict"):
            result = result.to_dict()
        self.output.setPlainText(json.dumps(result, ensure_ascii=False, indent=2, default=str))

    def clear_result(self) -> None:
        self.output.clear()

    def set_busy(self, busy: bool) -> None:
        self.busy.setVisible(busy)
        self.run_button.setEnabled(not busy)
        self.start_node.setEnabled(not busy)
        self.end_node.setEnabled(not busy)
        if busy:
            self.output.setPlainText("Analyzing resistance path...")


__all__ = ["PagedTablePane", "PathPane", "SummaryPane"]
