"""Jobs tab: filtered job presentation and action-cell rendering."""

from __future__ import annotations

from PyQt5.QtCore import QSignalBlocker, Qt
from PyQt5.QtWidgets import (
    QComboBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)
from cadgui.wheel import install_wheel_forwarding, remove_wheel_forwarding
from ..model import ClusterSnapshot
from .model_tables import JobTableModel
from .model_filters import JobFilterProxyModel
from .model_delegates import JobActionDelegate
from .table_view import table_view


def job_collection_error(snapshot: ClusterSnapshot | None) -> str:
    if snapshot is None:
        return ""
    return next(
        (
            item.message
            for item in snapshot.diagnostics
            if item.code == "job_collection_failed"
        ),
        "",
    )


class JobsPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.job_model = JobTableModel(self)
        self.job_proxy = JobFilterProxyModel(self)
        self.job_proxy.setSourceModel(self.job_model)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)
        job_filters = QHBoxLayout()
        job_filters.setSpacing(6)
        self.job_search_edit = QLineEdit(self)
        self.job_search_edit.setObjectName("jobSearch")
        self.job_search_edit.setPlaceholderText("Filter jobs")
        self.job_search_edit.setClearButtonEnabled(True)
        self.job_search_edit.setMaximumWidth(320)
        job_filters.addWidget(self.job_search_edit)
        self.job_status_combo = QComboBox(self)
        self.job_status_combo.setObjectName("jobStatusFilter")
        self.job_status_combo.addItem("All states")
        self.job_status_combo.setMinimumWidth(120)
        job_filters.addWidget(self.job_status_combo)
        job_filters.addStretch(1)
        self.job_count_label = QLabel("0 jobs", self)
        self.job_count_label.setObjectName("jobCountLabel")
        job_filters.addWidget(self.job_count_label)
        outer.addLayout(job_filters)
        self.job_table = table_view("jobTable")
        self.job_table.setModel(self.job_proxy)
        self.job_table.setSortingEnabled(True)
        self.job_table.sortByColumn(0, Qt.DescendingOrder)
        job_header = self.job_table.horizontalHeader()
        job_header.setStretchLastSection(False)
        job_header.setSectionResizeMode(QHeaderView.Interactive)
        job_header.setSectionResizeMode(8, QHeaderView.Stretch)
        job_header.setSectionResizeMode(9, QHeaderView.Fixed)
        job_widths = (82, 72, 110, 130, 58, 92, 125, 125, 280, 72)
        for column, width in enumerate(job_widths):
            self.job_table.setColumnWidth(column, width)
        self.job_action_delegate = JobActionDelegate(self.job_table)
        self.job_table.setItemDelegateForColumn(9, self.job_action_delegate)
        outer.addWidget(self.job_table, 1)

        self._wheel = install_wheel_forwarding(self.job_table)
        self.job_search_edit.textChanged.connect(self.job_proxy.set_query)
        self.job_status_combo.currentTextChanged.connect(self.job_proxy.set_status)

    def apply_snapshot(self, snapshot: ClusterSnapshot) -> None:
        failed = any(
            item.code == "job_collection_failed" for item in snapshot.diagnostics
        )
        if not failed:
            self.job_model.set_rows(snapshot.jobs)
            self._update_job_statuses(snapshot)
        self.update_count(snapshot)

    def shutdown(self) -> None:
        remove_wheel_forwarding(self._wheel)
        self.job_action_delegate.set_enabled(False)

    def _update_job_statuses(self, snapshot: ClusterSnapshot) -> None:
        statuses = sorted({job.status for job in snapshot.jobs}, key=str.casefold)
        previous = self.job_status_combo.currentText()
        blocker = QSignalBlocker(self.job_status_combo)
        self.job_status_combo.clear()
        self.job_status_combo.addItem("All states")
        self.job_status_combo.addItems(statuses)
        if previous in statuses:
            self.job_status_combo.setCurrentText(previous)
        del blocker
        self.job_proxy.set_status(self.job_status_combo.currentText())

    def update_count(self, snapshot: ClusterSnapshot | None) -> None:
        error = job_collection_error(snapshot)
        count = self.job_proxy.rowCount()
        total = self.job_model.rowCount()
        text = f"{count} job" if count == 1 else f"{count} jobs"
        if count != total:
            text += f" of {total}"
        if snapshot is not None:
            text += f" for {snapshot.user}"
        if error:
            text += " (refresh unavailable)"
        self.job_count_label.setText(text)
        self.job_count_label.setToolTip(error)
