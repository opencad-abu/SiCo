"""Common table view defaults for the LSF monitor."""

from PyQt5.QtWidgets import QAbstractItemView, QTableView


def table_view(name: str) -> QTableView:
    table = QTableView()
    table.setObjectName(name)
    table.setAlternatingRowColors(True)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setSelectionMode(QAbstractItemView.SingleSelection)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(28)
    table.horizontalHeader().setMinimumSectionSize(44)
    return table
