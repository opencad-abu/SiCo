"""Construction of the DSPF main-window workspace."""

from __future__ import annotations

from dataclasses import dataclass

from PyQt5.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from PyQt5.QtCore import Qt

from .widgets import PagedTablePane, PathPane, SummaryPane
from .network_pane import RcNetworkPane


@dataclass
class WorkspaceWidgets:
    root: QSplitter
    tabs: QTabWidget
    search: QLineEdit
    net_pane: PagedTablePane
    summary: SummaryPane
    network_pane: RcNetworkPane
    node_pane: PagedTablePane
    resistor_pane: PagedTablePane
    capacitor_pane: PagedTablePane
    diagnostic_pane: PagedTablePane
    path_pane: PathPane


def build_workspace(models: dict[str, object]) -> WorkspaceWidgets:
    search = QLineEdit()
    search.setClearButtonEnabled(True)
    search.setPlaceholderText("Filter nets by name")
    search.setMinimumHeight(28)
    net_pane = PagedTablePane(models["nets"], sortable=True)
    net_pane.table.setContextMenuPolicy(Qt.CustomContextMenu)
    net_pane.table.setColumnWidth(0, 72)
    net_pane.table.setColumnWidth(1, 210)
    net_pane.table.setColumnWidth(2, 120)
    net_pane.table.setColumnWidth(3, 90)

    left = QWidget()
    left_layout = QVBoxLayout(left)
    left_layout.setContentsMargins(8, 8, 4, 8)
    left_layout.setSpacing(6)
    filter_row = QHBoxLayout()
    filter_row.addWidget(QLabel("Nets:"))
    filter_row.addWidget(search, 1)
    left_layout.addLayout(filter_row)
    left_layout.addWidget(net_pane, 1)

    summary = SummaryPane()
    network_pane = RcNetworkPane()
    node_pane = PagedTablePane(models["nodes"])
    resistor_pane = PagedTablePane(models["resistors"])
    capacitor_pane = PagedTablePane(models["capacitors"])
    diagnostic_pane = PagedTablePane(models["diagnostics"])
    path_pane = PathPane()
    tabs = QTabWidget()
    tabs.addTab(summary, "Summary")
    tabs.addTab(network_pane, "R Network")
    tabs.addTab(node_pane, "Nodes")
    tabs.addTab(resistor_pane, "Resistors")
    tabs.addTab(capacitor_pane, "Capacitors")
    tabs.addTab(diagnostic_pane, "Diagnostics")
    tabs.addTab(path_pane, "Path")

    right = QWidget()
    right_layout = QVBoxLayout(right)
    right_layout.setContentsMargins(4, 8, 8, 8)
    right_layout.addWidget(tabs)
    splitter = QSplitter(Qt.Horizontal)
    splitter.addWidget(left)
    splitter.addWidget(right)
    splitter.setChildrenCollapsible(False)
    splitter.setSizes((440, 840))
    return WorkspaceWidgets(
        root=splitter,
        tabs=tabs,
        search=search,
        net_pane=net_pane,
        summary=summary,
        network_pane=network_pane,
        node_pane=node_pane,
        resistor_pane=resistor_pane,
        capacitor_pane=capacitor_pane,
        diagnostic_pane=diagnostic_pane,
        path_pane=path_pane,
    )


__all__ = ["WorkspaceWidgets", "build_workspace"]
