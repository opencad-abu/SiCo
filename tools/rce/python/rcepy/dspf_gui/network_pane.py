"""Controls and state presentation for the resistance network canvas."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from PyQt5.QtCore import QRectF, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPen
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDoubleSpinBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .network_canvas import RcNetworkCanvas
from .network_format import engineering as _engineering


_MAX_INTEGRITY_ROWS = 100


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _bounded_names(values: Any, limit: int = 8) -> str:
    names = [str(value) for value in (values or ())]
    shown = ", ".join(names[:limit])
    return f"{shown} (+{len(names) - limit})" if len(names) > limit else shown


def _endpoint(candidate: Any, prefix: str) -> str:
    name = str(_field(candidate, f"{prefix}_name", "?"))
    owner = _field(candidate, f"{prefix}_net_name")
    return f"{name} [{owner}]" if owner else name


class NetworkLegend(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(52)
        self.description = ""

    def set_description(self, text: str) -> None:
        self.description = text
        self.setToolTip(text)
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QColor("#4b5563"))
        painter.drawText(
            QRectF(4, 1, self.width() - 8, 32),
            Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap,
            self.description,
        )
        width = max(1, self.width() - 8)
        bar = QRectF(4, 38, width, 9)
        gradient = QLinearGradient(bar.topLeft(), bar.topRight())
        for offset, color in enumerate(
            (
                "#2166ac",
                "#258f8b",
                "#65a64a",
                "#c5bd32",
                "#ed9b27",
                "#df694c",
                "#b42318",
            )
        ):
            gradient.setColorAt(offset / 6, QColor(color))
        painter.fillRect(bar, gradient)
        painter.setPen(QPen(QColor("#8b949e"), 1))
        painter.drawRect(bar)


def _tool_button(
    parent: QWidget, theme: str, fallback: str, tooltip: str
) -> QToolButton:
    button = QToolButton(parent)
    icon = QIcon.fromTheme(theme)
    if icon.isNull():
        button.setText(fallback)
    else:
        button.setIcon(icon)
    button.setToolTip(tooltip)
    button.setAccessibleName(tooltip)
    button.setFixedSize(28, 28)
    return button


class RcNetworkPane(QWidget):
    """Feature-complete R network tab, independent of query orchestration."""

    integrityRequested = pyqtSignal(float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.network: Any | None = None
        self.layout: Any | None = None
        self.canvas = RcNetworkCanvas(self)

        self.labels = QCheckBox("Labels", self)
        self.labels.setToolTip("Show positioned node labels")
        self.labels.toggled.connect(self.canvas.set_labels)
        self.zoom_out = _tool_button(self, "zoom-out", "-", "Zoom out")
        self.zoom_in = _tool_button(self, "zoom-in", "+", "Zoom in")
        self.fit_button = _tool_button(self, "zoom-fit-best", "Fit", "Fit network")
        self.zoom_out.clicked.connect(lambda: self.canvas.zoom_by(1 / 1.25))
        self.zoom_in.clicked.connect(lambda: self.canvas.zoom_by(1.25))
        self.fit_button.clicked.connect(self.canvas.fit_network)

        threshold_label = QLabel("Short <=", self)
        self.short_threshold = QDoubleSpinBox(self)
        self.short_threshold.setRange(0.0, 1.0e12)
        self.short_threshold.setDecimals(6)
        self.short_threshold.setSingleStep(0.01)
        self.short_threshold.setValue(0.1)
        self.short_threshold.setSuffix(" ohm")
        self.short_threshold.setToolTip(
            "Maximum cross-net or ground-bridge resistance reported as a short"
        )
        self.short_threshold.setFixedWidth(132)
        self.check_integrity = QToolButton(self)
        self.check_integrity.setText("Check")
        self.check_integrity.setToolTip("Check resistance opens and shorts")
        self.check_integrity.setAccessibleName("Check resistance opens and shorts")
        self.check_integrity.setToolButtonStyle(
            Qt.ToolButtonTextBesideIcon
        )
        check_icon = QIcon.fromTheme("dialog-ok")
        if not check_icon.isNull():
            self.check_integrity.setIcon(check_icon)
        self.check_integrity.setFixedHeight(28)
        self.check_integrity.clicked.connect(
            lambda: self.integrityRequested.emit(self.short_threshold.value())
        )

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(6)
        controls.addWidget(self.labels)
        controls.addStretch(1)
        controls.addWidget(threshold_label)
        controls.addWidget(self.short_threshold)
        controls.addWidget(self.check_integrity)
        controls.addSpacing(8)
        controls.addWidget(self.zoom_out)
        controls.addWidget(self.zoom_in)
        controls.addWidget(self.fit_button)

        self.state_text = QLabel("Select a net to view its R network.", self)
        self.state_text.setAlignment(Qt.AlignCenter)
        self.state_text.setWordWrap(True)
        self.busy = QProgressBar(self)
        self.busy.setRange(0, 0)
        self.busy.setFixedWidth(160)
        self.busy.setTextVisible(False)
        self.busy.hide()
        state = QWidget(self)
        state_layout = QVBoxLayout(state)
        state_layout.addStretch(1)
        state_layout.addWidget(self.state_text)
        state_layout.addWidget(self.busy, 0, Qt.AlignHCenter)
        state_layout.addStretch(1)
        self.stack = QStackedWidget(self)
        self.stack.addWidget(self.canvas)
        self.stack.addWidget(state)
        self.stack.setCurrentWidget(state)

        self.legend = NetworkLegend(self)
        self.legend.set_description("Select a net")
        self.footer = QLabel("No R network loaded", self)
        self.footer.setTextInteractionFlags(
            Qt.TextSelectableByMouse
        )

        self.integrity_status = QLabel("Open/Short: not checked", self)
        self.integrity_status.setTextInteractionFlags(
            Qt.TextSelectableByMouse
        )
        self.integrity_status.setWordWrap(True)
        self.integrity_busy = QProgressBar(self)
        self.integrity_busy.setRange(0, 0)
        self.integrity_busy.setFixedWidth(90)
        self.integrity_busy.setTextVisible(False)
        self.integrity_busy.hide()
        integrity_header = QHBoxLayout()
        integrity_header.setContentsMargins(0, 0, 0, 0)
        integrity_header.addWidget(self.integrity_status, 1)
        integrity_header.addWidget(self.integrity_busy)
        self.integrity_issues = QTableWidget(0, 5, self)
        self.integrity_issues.setHorizontalHeaderLabels(
            ("Check", "Item", "Value", "Endpoints / terminals", "Source")
        )
        self.integrity_issues.setEditTriggers(
            QAbstractItemView.NoEditTriggers
        )
        self.integrity_issues.setSelectionBehavior(
            QAbstractItemView.SelectRows
        )
        self.integrity_issues.setAlternatingRowColors(True)
        self.integrity_issues.verticalHeader().hide()
        header = self.integrity_issues.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        self.integrity_issues.setMaximumHeight(132)
        self.integrity_issues.hide()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 6)
        layout.setSpacing(5)
        layout.addLayout(controls)
        layout.addWidget(self.stack, 1)
        layout.addWidget(self.legend)
        layout.addLayout(integrity_header)
        layout.addWidget(self.integrity_issues)
        layout.addWidget(self.footer)

    def set_network(self, network: Any, layout: Any | None) -> None:
        self.network, self.layout = network, layout
        self.busy.hide()
        self.footer.setText(self._footer_text())
        self.footer.setToolTip(self.footer.text())
        if network.status != "ok":
            self.canvas.clear_network()
            self._show_state(network.message or f"R network status: {network.status}")
        elif layout is None or not layout.positions:
            self.canvas.clear_network()
            self._show_state("No positioned nodes are available for this net.")
        else:
            self.canvas.set_network(network, layout)
            self.canvas.set_labels(self.labels.isChecked())
            self.stack.setCurrentWidget(self.canvas)
        self._update_legend()

    def clear(self) -> None:
        self.network = self.layout = None
        self.canvas.clear_network()
        self.busy.hide()
        self.footer.setText("No R network loaded")
        self.legend.set_description("Select a net")
        self.clear_integrity()
        self._show_state("Select a net to view its R network.")

    def set_busy(self, busy: bool) -> None:
        self.busy.setVisible(busy)
        if busy:
            self._show_state("Loading R network...")

    def set_error(self, message: str) -> None:
        self.network = self.layout = None
        self.canvas.clear_network()
        self.busy.hide()
        self.footer.setText("R network unavailable")
        self.legend.set_description("Query failed")
        self._show_state(message)

    def set_integrity_busy(self, busy: bool) -> None:
        self.integrity_busy.setVisible(busy)
        self.check_integrity.setEnabled(not busy)
        if busy:
            self.integrity_issues.setRowCount(0)
            self.integrity_issues.hide()
            self.integrity_status.setText("Open/Short: checking resistance network...")

    def set_integrity_error(self, message: str) -> None:
        self.integrity_busy.hide()
        self.check_integrity.setEnabled(True)
        self.integrity_issues.setRowCount(0)
        self.integrity_issues.hide()
        self.integrity_status.setText(f"Open/Short query failed: {message}")
        self.integrity_status.setToolTip(message)

    def clear_integrity(self) -> None:
        self.integrity_busy.hide()
        self.check_integrity.setEnabled(True)
        self.integrity_issues.setRowCount(0)
        self.integrity_issues.hide()
        self.integrity_status.setText("Open/Short: not checked")
        self.integrity_status.setToolTip("")

    def set_integrity_result(self, result: Any) -> None:
        self.integrity_busy.hide()
        self.check_integrity.setEnabled(True)
        status = str(_field(result, "status", "ok"))
        message = _field(result, "message")
        if status != "ok" and message:
            self.integrity_status.setText(f"Open/Short {status}: {message}")
            self.integrity_status.setToolTip(str(message))
        else:
            self.integrity_status.setText(self._integrity_summary(result))
            self.integrity_status.setToolTip(str(message or ""))
        rows = self._integrity_rows(result)
        self.integrity_issues.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.integrity_issues.setItem(row, column, item)
        self.integrity_issues.setVisible(bool(rows))

    @staticmethod
    def _integrity_summary(result: Any) -> str:
        threshold = float(_field(result, "short_threshold_ohm", 0.0))
        open_status = str(_field(result, "open_status", "unknown")).upper()
        short_status = str(_field(result, "short_status", "unknown")).upper()
        terminal_count = int(_field(result, "terminal_count", 0))
        component_count = int(_field(result, "terminal_component_count", 0))
        islands = int(_field(result, "island_count", 0))
        orphans = int(_field(result, "orphan_node_count", 0))
        candidates = int(_field(result, "short_candidate_count", 0))
        boundary = int(_field(result, "inconclusive_boundary_count", 0))
        component_word = "component" if component_count == 1 else "components"
        return (
            f"Open: {open_status} ({terminal_count} terminals / "
            f"{component_count} {component_word}; "
            f"{islands} islands, {orphans} orphans) | Short: {short_status} "
            f"({candidates} candidates, R <= {_engineering(threshold, 'ohm')}; "
            f"{boundary} boundary inconclusive)"
        )

    @staticmethod
    def _integrity_rows(result: Any) -> list[tuple[str, ...]]:
        rows: list[tuple[str, ...]] = []
        if _field(result, "open_status") == "open":
            for number, component in enumerate(
                _field(result, "open_components", ()) or (), 1,
            ):
                rows.append((
                    "Open", f"Component {number}",
                    f"{int(_field(component, 'node_count', 0)):,} nodes",
                    _bounded_names(_field(component, "terminal_names", ())), "",
                ))
        for candidate in _field(result, "short_candidates", ()) or ():
            value = float(_field(candidate, "value", 0.0))
            source_line = _field(candidate, "source_line")
            rows.append((
                "Short", str(_field(candidate, "name", "unnamed")),
                _engineering(value, "ohm"),
                f"{_endpoint(candidate, 'node1')} -> {_endpoint(candidate, 'node2')}",
                "" if source_line is None else f"line {source_line}",
            ))
        for field, label in (
            ("negative_resistor_count", "Negative resistors"),
            ("zero_resistor_count", "Zero-ohm resistors"),
            ("self_loop_count", "Resistor self-loops"),
            ("inconclusive_boundary_count", "Boundary candidates"),
        ):
            count = int(_field(result, field, 0))
            if count:
                rows.append(("Topology", label, f"{count:,}", "", ""))
        return rows[:_MAX_INTEGRITY_ROWS]

    def _show_state(self, message: str) -> None:
        self.state_text.setText(message)
        self.state_text.setToolTip(message)
        self.stack.setCurrentIndex(1)

    def _update_legend(self) -> None:
        if self.network is None or self.network.status != "ok":
            return
        low, high, unit = self.canvas.legend_range()
        self.legend.set_description(
            _range_text(low, high, unit) + " | squares: external R endpoints"
        )

    def _footer_text(self) -> str:
        visible = (
            "not counted"
            if self.network.visible_node_count is None
            else f"{self.network.visible_node_count:,} visible"
        )
        return (
            f"{self.network.name} | {self.network.owned_node_count:,} owned / {visible} nodes | "
            f"{self.network.coordinate_node_count:,} physical | {self.network.resistor_count:,} R | "
            f"{self.layout.mode if self.layout is not None else 'not computed'} layout"
        )


def _range_text(low: float | None, high: float | None, unit: str) -> str:
    if low is None or high is None:
        return f"no positive {unit} values"
    return f"{_engineering(low, unit)} to {_engineering(high, unit)} (log scale)"


__all__ = ["NetworkLegend", "RcNetworkPane"]
