"""Modeless browser for generated MTS netlists and published OA views."""

from __future__ import annotations

from dataclasses import dataclass, replace
import os
from pathlib import Path
import shutil
import subprocess
from typing import Callable, Mapping

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QDialogButtonBox,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from cadgui.branding import logo_text
from cadgui.chrome import SiDialog


@dataclass(frozen=True)
class ResultView:
    library: str
    cell: str
    view: str

    @property
    def label(self) -> str:
        return "/".join((self.library, self.cell, self.view))


@dataclass(frozen=True)
class ResultRow:
    source_library: str
    source_cell: str
    source_view: str
    netlist_file: Path
    symbol_view: ResultView | None = None
    netlist_view: ResultView | None = None

    @property
    def source_key(self) -> tuple[str, str, str]:
        return (self.source_library, self.source_cell, self.source_view)

    @property
    def source_label(self) -> str:
        return "/".join(self.source_key)

    def with_publication(self, publication: object) -> "ResultRow":
        """Add only views that a publication result confirms as successful."""

        symbol = getattr(publication, "symbol", None)
        text = getattr(publication, "text", None)
        target_library = str(getattr(publication, "target_library", ""))
        target_cell = str(getattr(publication, "target_cell", ""))
        symbol_view = self.symbol_view
        netlist_view = self.netlist_view
        if symbol is not None and getattr(symbol, "status", "") == "succeeded":
            symbol_view = ResultView(target_library, target_cell, "symbol")
        if text is not None and getattr(text, "status", "") == "succeeded":
            netlist_view = ResultView(
                target_library,
                target_cell,
                str(getattr(text, "target_view", "")),
            )
        return replace(
            self,
            symbol_view=symbol_view,
            netlist_view=netlist_view,
        )


def open_netlist_file(
    path: str | Path,
    *,
    environ: Mapping[str, str] | None = None,
) -> int:
    """Open a generated netlist in gvim without invoking a shell."""

    source = Path(path).expanduser().resolve()
    if not source.is_file() or not os.access(source, os.R_OK):
        raise FileNotFoundError(f"generated netlist is unavailable: {source}")
    environment = os.environ if environ is None else environ
    search_path = environment.get("PATH")
    wrapper = shutil.which("runUserCmd", path=search_path)
    if wrapper:
        argv = [wrapper, "gvim", "--", str(source)]
    else:
        editor = shutil.which("gvim", path=search_path)
        if editor is None:
            raise FileNotFoundError("cannot find runUserCmd or gvim in PATH")
        argv = [editor, "--", str(source)]
    process = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
        env=dict(environment),
    )
    return int(process.pid)


class ResultBrowserDialog(SiDialog):
    """Four-column, modeless result selector for one Process page."""

    HEADERS = ("Source Cell", "Netlist File", "Symbol View", "Netlist View")
    _PAYLOAD_ROLE = Qt.UserRole

    def __init__(
        self,
        rows: tuple[ResultRow, ...],
        *,
        open_view: Callable[[ResultView], object],
        report_error: Callable[[str], object],
        parent=None,
    ) -> None:
        super().__init__(f"{logo_text()}::MTS Results", parent, ("close",))
        self._open_view = open_view
        self._report_error = report_error
        self.setModal(False)
        self.resize(1100, 420)
        self.setMinimumSize(760, 300)

        layout = QVBoxLayout()
        self.content_layout().addLayout(layout)
        self.table = QTableWidget(0, len(self.HEADERS), self)
        self.table.setObjectName("mtsResultTable")
        self.table.setHorizontalHeaderLabels(list(self.HEADERS))
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(False)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.cellDoubleClicked.connect(self._open_cell)
        layout.addWidget(self.table, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close, parent=self)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)
        self.set_rows(rows)

    @staticmethod
    def _item(text: str, payload: object | None = None) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        if payload is None:
            item.setForeground(QColor("#7a8288"))
        else:
            item.setData(ResultBrowserDialog._PAYLOAD_ROLE, payload)
            item.setToolTip("Double-click to open")
        return item

    def set_rows(self, rows: tuple[ResultRow, ...]) -> None:
        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            source_item = QTableWidgetItem(row.source_label)
            self.table.setItem(index, 0, source_item)
            self.table.setItem(
                index,
                1,
                self._item(str(row.netlist_file), ("file", str(row.netlist_file))),
            )
            symbol = row.symbol_view
            self.table.setItem(
                index,
                2,
                self._item(
                    "-" if symbol is None else symbol.label,
                    None if symbol is None else ("view", symbol),
                ),
            )
            netlist = row.netlist_view
            self.table.setItem(
                index,
                3,
                self._item(
                    "-" if netlist is None else netlist.label,
                    None if netlist is None else ("view", netlist),
                ),
            )

    def _open_cell(self, row: int, column: int) -> None:
        item = self.table.item(row, column)
        payload = None if item is None else item.data(self._PAYLOAD_ROLE)
        if not isinstance(payload, tuple) or len(payload) != 2:
            return
        try:
            if payload[0] == "file":
                open_netlist_file(str(payload[1]))
            elif payload[0] == "view" and isinstance(payload[1], ResultView):
                self._open_view(payload[1])
        except Exception as exc:
            self._report_error(str(exc))


__all__ = [
    "ResultBrowserDialog",
    "ResultRow",
    "ResultView",
    "open_netlist_file",
]
