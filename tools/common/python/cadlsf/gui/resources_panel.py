"""Resources tab: queue choices, host presentation, and current Qt selection."""

from __future__ import annotations

from PyQt5.QtCore import QSignalBlocker, Qt
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QHeaderView,
    QSpinBox,
    QSplitter,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from cadgui.wheel import install_wheel_forwarding, remove_wheel_forwarding
from ..model import ClusterSnapshot, HostInfo
from .models import (
    QueueTableModel,
    HostTableModel,
    HostFilterProxyModel,
    ProgressCellDelegate,
)
from .table_view import table_view


class ResourcesPanel(QWidget):
    def __init__(self, refresh_interval: int, parent=None):
        super().__init__(parent)
        self.queue_model = QueueTableModel(self)
        self.host_model = HostTableModel(self)
        self.host_proxy = HostFilterProxyModel(self)
        self.host_proxy.setSourceModel(self.host_model)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 6)
        outer.setSpacing(6)
        splitter = QSplitter(Qt.Vertical, self)
        splitter.setChildrenCollapsible(False)
        self._build_queue_table()
        splitter.addWidget(self.queue_table)
        host_panel = QWidget(splitter)
        host_layout = QVBoxLayout(host_panel)
        host_layout.setContentsMargins(0, 6, 0, 0)
        host_layout.setSpacing(6)
        controls = self._build_controls(host_panel, refresh_interval)
        host_layout.addLayout(controls)

        self._build_host_table()
        host_layout.addWidget(self.host_table, 1)
        splitter.addWidget(host_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([140, 500])
        outer.addWidget(splitter, 1)

        self._wheels = tuple(
            install_wheel_forwarding(table)
            for table in (self.queue_table, self.host_table)
        )
        self.search_edit.textChanged.connect(self.host_proxy.set_query)
        self.available_only.toggled.connect(self.host_proxy.set_available_only)
        self.host_proxy.rowsInserted.connect(self._update_host_count)
        self.host_proxy.rowsRemoved.connect(self._update_host_count)
        self.host_proxy.modelReset.connect(self._update_host_count)

    def selected_queue(self) -> str:
        return self.queue_combo.currentText().strip()

    def selected_host(self) -> HostInfo | None:
        source = self.host_proxy.mapToSource(self.host_table.currentIndex())
        rows = self.host_model.rows
        return (
            rows[source.row()]
            if source.isValid() and 0 <= source.row() < len(rows)
            else None
        )

    def apply_snapshot(
        self, snapshot: ClusterSnapshot, preferred_queue: str | None
    ) -> None:
        self.queue_model.set_rows(snapshot.queues)
        self.host_model.set_rows(snapshot.hosts)
        self.sample_label.setText(snapshot.collected_at)
        self.sample_label.setToolTip(f"Collected for OS user {snapshot.user}")
        self._update_queue_choices(snapshot, preferred_queue)
        self._update_host_count()
        if self.host_proxy.rowCount() and not self.host_table.currentIndex().isValid():
            self.host_table.selectRow(0)

    def set_busy(self, busy: bool, closing: bool) -> None:
        self.refresh_button.setEnabled(not closing and not busy)
        self.reload_topology_button.setEnabled(not closing and not busy)

    def shutdown(self) -> None:
        for wheel in self._wheels:
            remove_wheel_forwarding(wheel)

    def _update_queue_choices(
        self, snapshot: ClusterSnapshot, preferred_queue: str | None
    ) -> None:
        names = [queue.name for queue in snapshot.queues]
        desired = snapshot.selected_queue or preferred_queue
        if desired not in names:
            desired = names[0] if names else None
        blocker = QSignalBlocker(self.queue_combo)
        self.queue_combo.clear()
        self.queue_combo.addItems(names)
        if desired is not None:
            self.queue_combo.setCurrentText(desired)
        del blocker
        self.queue_combo.setEnabled(bool(names))

    def _update_host_count(self, *_args) -> None:
        count = self.host_proxy.rowCount()
        total = self.host_model.rowCount()
        text = f"{count} host" if count == 1 else f"{count} hosts"
        if count != total:
            text += f" of {total}"
        self.host_count_label.setText(text)

    def _build_controls(self, host_panel, refresh_interval):
        controls = QHBoxLayout()
        controls.setSpacing(6)
        controls.addWidget(QLabel("Queue:", host_panel))
        self.queue_combo = QComboBox(host_panel)
        self.queue_combo.setObjectName("queueCombo")
        self.queue_combo.setMinimumWidth(180)
        self.queue_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.queue_combo.setEnabled(False)
        controls.addWidget(self.queue_combo)
        self.search_edit = QLineEdit(host_panel)
        self.search_edit.setObjectName("hostSearch")
        self.search_edit.setPlaceholderText("Filter hosts")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setMinimumWidth(140)
        self.search_edit.setMaximumWidth(320)
        controls.addWidget(self.search_edit)
        self.available_only = QCheckBox("Available only", host_panel)
        self.available_only.setObjectName("availableOnlyCheck")
        self.available_only.setChecked(True)
        controls.addWidget(self.available_only)
        self.auto_refresh = QCheckBox("Auto Refresh", host_panel)
        self.auto_refresh.setObjectName("autoRefreshCheck")
        self.auto_refresh.setChecked(True)
        controls.addWidget(self.auto_refresh)
        self.interval_spin = QSpinBox(host_panel)
        self.interval_spin.setObjectName("refreshIntervalSpin")
        self.interval_spin.setRange(2, 3600)
        self.interval_spin.setSuffix(" s")
        self.interval_spin.setValue(max(2, min(3600, refresh_interval)))
        self.interval_spin.setToolTip("Refresh interval")
        controls.addWidget(self.interval_spin)
        self.refresh_button = QToolButton(host_panel)
        self.refresh_button.setObjectName("refreshButton")
        self.refresh_button.setIcon(self.style().standardIcon(QStyle.SP_BrowserReload))
        self.refresh_button.setToolTip("Refresh now")
        self.refresh_button.setAutoRaise(True)
        controls.addWidget(self.refresh_button)
        self.reload_topology_button = QToolButton(host_panel)
        self.reload_topology_button.setObjectName("reloadTopologyButton")
        self.reload_topology_button.setIcon(
            self.style().standardIcon(QStyle.SP_FileDialogDetailedView)
        )
        self.reload_topology_button.setToolTip(
            "Reload queue, host membership, and capacity topology"
        )
        self.reload_topology_button.setAutoRaise(True)
        controls.addWidget(self.reload_topology_button)
        controls.addStretch(1)
        self.host_count_label = QLabel("0 hosts", host_panel)
        self.host_count_label.setObjectName("hostCountLabel")
        controls.addWidget(self.host_count_label)
        controls.addWidget(QLabel("Sample:", host_panel))
        self.sample_label = QLabel("-", host_panel)
        self.sample_label.setObjectName("sampleLabel")
        self.sample_label.setMinimumWidth(170)
        controls.addWidget(self.sample_label)
        return controls

    def _build_host_table(self):
        self.host_table = table_view("hostTable")
        self.host_table.setModel(self.host_proxy)
        self.host_table.setSortingEnabled(True)
        self.host_table.sortByColumn(0, Qt.AscendingOrder)
        delegate = ProgressCellDelegate(self.host_table)
        for column in (3, 8, 9, 10):
            self.host_table.setItemDelegateForColumn(column, delegate)
        header = self.host_table.horizontalHeader()
        header.setStretchLastSection(True)
        widths = (48, 135, 62, 95, 54, 54, 54, 54, 78, 112, 96)
        for column, width in enumerate(widths):
            self.host_table.setColumnWidth(column, width)

    def _build_queue_table(self):
        self.queue_table = table_view("queueTable")
        self.queue_table.setModel(self.queue_model)
        self.queue_table.setSortingEnabled(True)
        self.queue_table.sortByColumn(0, Qt.AscendingOrder)
        queue_header = self.queue_table.horizontalHeader()
        queue_header.setStretchLastSection(False)
        queue_header.setSectionResizeMode(QHeaderView.Stretch)
        self.queue_table.setMaximumHeight(180)
