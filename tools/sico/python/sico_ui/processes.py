"""Compact process page; all collection and log reads belong to data workers."""

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QToolButton,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .receipts import DataReceipt


class ProcessPanel(QWidget):
    activated = pyqtSignal(str, str)

    def __init__(self, source):
        super().__init__()
        self.source = source
        self.offset = self.total = self.generation = 0
        self.active = True
        self.receipt = DataReceipt(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit()
        self.search.setMaxLength(256)
        self.search.setPlaceholderText("搜索进程、PID 或命令")
        self.search.textChanged.connect(self.search_changed)
        layout.addWidget(self.search)
        self.list = QTreeWidget()
        self.list.setObjectName("processList")
        self.list.setHeaderLabels(["进程", "PID", "状态"])
        self.list.setRootIsDecorated(False)
        self.list.setUniformRowHeights(True)
        self.list.setVerticalScrollMode(QTreeWidget.ScrollPerPixel)
        self.list.header().setStretchLastSection(False)
        self.list.header().setSectionResizeMode(0, QHeaderView.Stretch)
        for column in (1, 2):
            self.list.header().setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.list.itemClicked.connect(self.open_item)
        self.list.itemActivated.connect(self.open_item)
        layout.addWidget(self.list, 1)
        self.summary = QLabel("暂无 SiCo 启动的进程")
        self.summary.setTextFormat(Qt.PlainText)
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        paging = QHBoxLayout()
        self.previous, self.next = QToolButton(), QToolButton()
        self.previous.setText("上一页")
        self.next.setText("下一页")
        self.previous.clicked.connect(lambda: self.page(-1))
        self.next.clicked.connect(lambda: self.page(1))
        self.previous.setEnabled(False)
        self.next.setEnabled(False)
        paging.addWidget(self.previous)
        paging.addStretch(1)
        paging.addWidget(self.next)
        layout.addLayout(paging)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.poll)
        self.timer.start()

    def invalidate(self):
        # This is also called during QObject destruction; do not touch Qt here.
        self.active = False
        self.generation += 1

    def suspend(self):
        self.invalidate()
        self.receipt.clear()

    def bind(self, source):
        if source is not self.source:
            self.suspend()
            self.source = source
            self.list.clear()
            self.offset = self.total = 0
            self.search.blockSignals(True)
            self.search.clear()
            self.search.blockSignals(False)
            self.summary.setText("暂无 SiCo 启动的进程")
        self.active = True
        self.poll()

    def search_changed(self, *_args):
        self.offset = 0
        self.generation += 1
        self.receipt.clear()
        self.poll()

    def page(self, step):
        self.offset = max(0, self.offset + 200 * step)
        self.generation += 1
        self.receipt.clear()
        self.poll()

    def poll(self):
        if not self.active or not self.isVisible() or self.receipt.pending:
            return
        source, generation = self.source, self.generation

        def current():
            return self.active and source is self.source and generation == self.generation

        def ready(result):
            if current():
                source.validate_current()
                self.render(result)

        def failed(_exc):
            if current():
                self.summary.setText("进程数据暂不可读，稍后自动重试。")

        try:
            self.receipt.watch(source.processes(self.search.text(), self.offset), ready, failed)
        except (ValueError, RuntimeError) as exc:
            failed(exc)

    def render(self, result):
        self.offset, self.total = result["offset"], result["total"]
        chosen = self.list.currentItem()
        selected = chosen.data(0, Qt.UserRole) if chosen else None
        position = self.list.verticalScrollBar().value()
        existing = {self.list.topLevelItem(n).data(0, Qt.UserRole): self.list.topLevelItem(n)
                    for n in range(self.list.topLevelItemCount())}
        self.list.blockSignals(True)
        # Reuse items so polling does not reset keyboard focus or selection.
        for row_number, row in enumerate(result["rows"]):
            item = existing.pop(row["id"], None) or QTreeWidgetItem()
            old = self.list.indexOfTopLevelItem(item)
            if old != row_number:
                if old >= 0:
                    self.list.takeTopLevelItem(old)
                self.list.insertTopLevelItem(row_number, item)
            item.setData(0, Qt.UserRole, row["id"])
            for column, value in enumerate((row["name"], str(row["pid"] or "—"), row["state_label"])):
                item.setText(column, value)
            item.setToolTip(0, row["command"] + "\n运行时长：" + row["elapsed"])
            item.setToolTip(1, str(row["pid"]) if row["pid"] else "此命令未提供可确认的系统 PID")
            if row["id"] == selected:
                self.list.setCurrentItem(item)
        for item in existing.values():
            self.list.takeTopLevelItem(self.list.indexOfTopLevelItem(item))
        self.list.blockSignals(False)
        self.list.verticalScrollBar().setValue(position)
        self.previous.setEnabled(self.offset > 0)
        self.next.setEnabled(self.offset + 200 < self.total)
        self.summary.setText((f"运行中 {result['running']} · 共 {self.total} 条"
                              + (f" · {self.offset + 1}–{min(self.offset + 200, self.total)}"
                                 if self.total > 200 else "")) if self.total else
                             "没有匹配的进程" if self.search.text() else "暂无 SiCo 启动的进程")

    def open_item(self, item, _column=0):
        if self.active and item is not None:
            self.activated.emit("process", item.data(0, Qt.UserRole))

    def reveal(self, key):
        for n in range(self.list.topLevelItemCount()):
            item = self.list.topLevelItem(n)
            if item.data(0, Qt.UserRole) == key:
                self.list.setCurrentItem(item)
                break

    def showEvent(self, event):
        super().showEvent(event)
        self.poll()

    def closeEvent(self, event):
        self.suspend()
        self.timer.stop()
        super().closeEvent(event)
