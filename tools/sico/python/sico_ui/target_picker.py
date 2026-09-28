"""Linked library/cell/view picker for opening a design target window.

The 审阅 question only offers single choices, so ``新开窗口…`` hands over to this
dialog: the three lists stay linked, views are filtered to the requested
editor family, and an already-open view is marked instead of being offered.
"""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QStyle,
    QToolButton,
    QVBoxLayout,
)

from sico.service.audit import NEW_WINDOW_OPTION as _NEW_WINDOW_OPTION

from .chrome import SiDialog
from .presentation import BusyCursor
from .receipts import DataReceipt

SCHEMATIC_FAMILY = ("schematic", "schematicXL", "schematicVXL")
LAYOUT_FAMILY = ("maskLayout", "maskLayoutXL", "maskLayoutGXL")
NEW_WINDOW_OPTION = _NEW_WINDOW_OPTION


class TargetPicker(SiDialog):
    """Present asynchronous target lookups and close only after a successful open."""

    def __init__(self, provider, family=SCHEMATIC_FAMILY, parent=None, *, valid=lambda: True):
        super().__init__("打开设计视图", parent)
        self.provider, self.family = provider, tuple(family)
        self._valid, self._query = valid, 0
        self.selection = None
        self._loading = ""
        self._opening = False
        self._finished = False
        self._error = ""
        self._retry = self.reload_libraries
        self.receipt = DataReceipt(self)
        # 加载库/单元/视图与打开目标期间，busy 只出现在这个对话框里。
        self._busy = BusyCursor(self)
        self.destroyed.connect(lambda *_args: provider.close())
        self.setMinimumWidth(420)
        layout = QVBoxLayout()
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(12)
        self.content_layout().addLayout(layout)
        form = QFormLayout()
        layout.addLayout(form)

        self.all_libraries = QCheckBox("显示全部库（含只读库）")
        self.all_libraries.toggled.connect(self.reload_libraries)
        layout.addWidget(self.all_libraries)

        self.library = QComboBox()
        self.cell = QComboBox()
        self.view = QComboBox()
        for combo in (self.library, self.cell, self.view):
            combo.setMinimumContentsLength(24)
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        form.addRow("库", self.library)
        form.addRow("单元", self.cell)
        form.addRow("视图", self.view)
        self.hint = QLabel("")
        self.hint.setTextFormat(Qt.PlainText)
        self.hint.setWordWrap(True)
        self.hint.setMinimumHeight(self.hint.fontMetrics().height() * 2)
        status = QHBoxLayout()
        status.addWidget(self.hint, 1)
        self.retry = QToolButton()
        self.retry.setIcon(self.style().standardIcon(QStyle.SP_BrowserReload))
        self.retry.setToolTip("重新加载")
        self.retry.clicked.connect(lambda: self._retry())
        status.addWidget(self.retry)
        layout.addLayout(status)

        self.read_only = QCheckBox("只读打开（默认可编辑）")
        layout.addWidget(self.read_only)

        self.buttons = QDialogButtonBox()
        self.open_button = self.buttons.addButton("打开并绑定", QDialogButtonBox.AcceptRole)
        self.create_button = self.buttons.addButton("创建并打开", QDialogButtonBox.ActionRole)
        self.buttons.addButton("取消", QDialogButtonBox.RejectRole)
        self.open_button.clicked.connect(self.accept)
        self.create_button.clicked.connect(self.create_view)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self.library.currentIndexChanged.connect(self.reload_cells)
        self.cell.currentIndexChanged.connect(self.reload_views)
        self.view.currentIndexChanged.connect(self.refresh_buttons)
        self.reload_libraries()

    def _clear(self, *combos):
        for combo in combos:
            combo.blockSignals(True)
            combo.clear()
            combo.blockSignals(False)

    def _request(self, stage, operation, ready):
        if self._finished or not self._valid():
            return
        self._query += 1
        query = self._query
        self.receipt.clear()
        self._loading, self._error = stage, ""
        self.refresh_buttons()

        def loaded(result):
            if self._finished or query != self._query:
                return
            if not self._valid():
                self.reject()
                return
            self._loading = ""
            ready(result)

        def failed(exc):
            if self._finished or query != self._query:
                return
            if not self._valid():
                self.reject()
                return
            self._failed(exc)

        try:
            future = operation()
        except Exception as exc:
            failed(exc)
        else:
            self.receipt.watch(future, loaded, failed)

    def _failed(self, exc):
        opening = self._opening
        self._loading, self._opening = "", False
        self._error = ("打开设计目标失败：" if opening else "加载失败：") + str(exc)
        self.refresh_buttons()

    def reload_libraries(self, *_args):
        if self._opening or self._finished:
            return
        self._clear(self.library, self.cell, self.view)
        self._retry = self.reload_libraries
        self._request("libraries", lambda: self.provider.libraries(self.all_libraries.isChecked()),
                      self._libraries_loaded)

    def _libraries_loaded(self, entries):
        self.library.blockSignals(True)
        for entry in entries:
            label = entry["name"] if entry.get("writable", True) else entry["name"] + "（只读）"
            self.library.addItem(label, entry)
        self.library.blockSignals(False)
        self.reload_cells()

    def reload_cells(self, *_args):
        if self._opening or self._finished:
            return
        self.receipt.clear()
        self._clear(self.cell, self.view)
        self._loading, self._error = "", ""
        self._retry = self.reload_cells
        entry = self.library.currentData()
        if entry:
            self._request("cells", lambda: self.provider.cells(entry["name"]), self._cells_loaded)
        else:
            self.refresh_buttons()

    def _cells_loaded(self, names):
        self.cell.blockSignals(True)
        for name in names:
            self.cell.addItem(name, name)
        self.cell.blockSignals(False)
        self.reload_views()

    def reload_views(self, *_args):
        if self._opening or self._finished:
            return
        self.receipt.clear()
        self._clear(self.view)
        self._loading, self._error = "", ""
        self._retry = self.reload_views
        entry, cell = self.library.currentData(), self.cell.currentData()
        if entry and cell:
            self._request("views", lambda: self.provider.views(entry["name"], cell, self.family),
                          self._views_loaded)
        else:
            self.refresh_buttons()

    def _views_loaded(self, views):
        self.view.blockSignals(True)
        for view in views:
            if view.get("view_type") not in self.family:
                continue
            label = view["name"]
            if view.get("open"):
                label += "（已打开）"
            self.view.addItem(label, view)
        self.view.blockSignals(False)
        self.refresh_buttons()

    def refresh_buttons(self, *_args):
        view, entry = self.view.currentData(), self.library.currentData()
        has_view = view is not None
        idle = not self._loading and not self._opening and not self._finished
        self._busy.set(bool(self._loading or self._opening))
        self.all_libraries.setEnabled(not self._opening)
        self.library.setEnabled(bool(self.library.count()) and not self._opening)
        self.cell.setEnabled(bool(self.cell.count()) and not self._opening)
        self.view.setEnabled(bool(self.view.count()) and not self._opening)
        self.read_only.setEnabled(not self._opening)
        self.retry.setEnabled(idle)
        self.retry.setVisible(bool(self._error) and not has_view)
        self.open_button.setEnabled(idle and has_view and not view.get("open"))
        creatable = (
            not has_view
            and self.cell.currentData() is not None
            and entry is not None
            and entry.get("writable", True)
            and self.provider.can_create
        )
        self.create_button.setEnabled(bool(creatable) and idle and not self._error)
        self.create_button.setVisible(bool(creatable))
        if self._opening:
            self.hint.setText("正在打开并绑定设计目标…")
        elif self._loading:
            self.hint.setText({"libraries": "正在加载库…", "cells": "正在加载单元…",
                               "views": "正在加载视图…"}[self._loading])
        elif self._error:
            self.hint.setText(self._error)
        elif not self.library.count():
            self.hint.setText("没有可用库（可勾选“显示全部库”）。")
        elif not self.cell.count():
            self.hint.setText("该库下没有可选的单元。")
        elif not self.view.count():
            self.hint.setText("该单元下没有同类视图。")
        elif view and view.get("open"):
            self.hint.setText("该视图已打开。")
        else:
            self.hint.setText("")

    def chosen_target(self):
        entry = self.library.currentData()
        cell, view = self.cell.currentData(), self.view.currentData()
        if not (entry and cell and view):
            return None
        return {
            "library": entry["name"], "cell": cell, "view": view["name"],
            "view_type": view.get("view_type", self.family[0]),
            "read_only": self.read_only.isChecked(),
        }

    def accept(self, _checked=False):
        if not self.open_button.isEnabled():
            return
        target = self.chosen_target()
        if target is not None:
            self._open(lambda: self.provider.open_target(target))

    def _open(self, operation):
        self._opening = True
        self._request("open", operation, self._opened)

    def _opened(self, target):
        if not isinstance(target, dict) or not all(
            target.get(key) for key in ("library", "cell", "view")
        ):
            raise ValueError("桥接没有返回完整的设计目标")
        self.selection = target
        self.done(QDialog.Accepted)

    def create_view(self, _checked=False):
        if not self.create_button.isEnabled():
            return
        target = self.chosen_target() or {
            "library": (self.library.currentData() or {}).get("name", ""),
            "cell": self.cell.currentData() or "",
            "view": "", "view_type": self.family[0], "read_only": False,
        }
        self._open(lambda: self.provider.create(target))

    def done(self, result):
        self._finished = True
        self.receipt.clear()
        self.provider.close()
        super().done(result)

    def reject(self):
        self.done(QDialog.Rejected)

    def closeEvent(self, event):
        self.reject()
        event.accept()
