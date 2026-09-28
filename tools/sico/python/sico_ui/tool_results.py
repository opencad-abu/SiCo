"""Replayable tool evidence with ADE tables and bounded field details."""

from __future__ import annotations

from PyQt5.QtCore import QByteArray, Qt
from PyQt5.QtSvg import QSvgWidget
from PyQt5.QtWidgets import (
    QLabel,
    QSplitter,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sico.service.tool_display import TITLES, result_html, table

from .presentation import BusyCursor, style_tables
from .receipts import DataReceipt
from .workspace import EvenTabWidget

__all__ = ("TITLES", "result_html", "table", "ToolResults")


class EvidenceView(QTextBrowser):
    def loadResource(self, resource_type, url):
        return None


class ToolResults(QSplitter):
    def __init__(self, source):
        super().__init__(Qt.Vertical)
        self.source = source
        self._receipt = DataReceipt(self)
        self._select_generation = 0
        self.records = {}
        self.items = {}
        self.epoch = 0
        self.list = QTreeWidget()
        self.list.setVerticalScrollMode(QTreeWidget.ScrollPerPixel)
        self.list.setHeaderLabels(["工具", "状态", "摘要"])
        self.list.setRootIsDecorated(False)
        self.list.currentItemChanged.connect(self.select)
        self.addWidget(self.list)
        self.details = EvenTabWidget()
        self.summary = EvidenceView()
        self.summary.setOpenLinks(False)
        self.summary.setOpenExternalLinks(False)
        self.fields = EvidenceView()
        self.fields.setOpenLinks(False)
        self.fields.setOpenExternalLinks(False)
        # 读取完整结果期间只给结果视图显示 busy。
        self._busy = BusyCursor(self.summary)
        self.details.addTab(self.summary, "结果表格")
        self.details.addTab(self.fields, "数据明细")
        preview = QWidget()
        preview_layout = QVBoxLayout(preview)
        self.preview_notice = QLabel("创建电路后显示原理图预览。")
        self.svg_preview = QSvgWidget()
        preview_layout.addWidget(self.preview_notice)
        preview_layout.addWidget(self.svg_preview, 1)
        self.details.addTab(preview, "原理图预览")
        self.addWidget(self.details)
        self.setSizes([160, 440])

    def clear(self, source):
        self.source = source
        self.records.clear()
        self.items.clear()
        self.epoch = 0
        self.list.clear()
        self.summary.clear()
        self.fields.clear()
        self.svg_preview.load(QByteArray())
        self._receipt.clear()
        self._busy.set(False)
        self._select_generation += 1

    def receive(self, kind, payload):
        if kind == "task.started":
            self.epoch += 1
        elif kind == "tool.started":
            key = f"{self.epoch}:{payload['id']}"
            item = QTreeWidgetItem([TITLES.get(payload["name"], payload["name"]), "执行中", ""])
            item.setData(0, Qt.UserRole, key)
            item.setData(0, Qt.UserRole + 1, payload["name"])
            self.items[key] = item
            self.list.addTopLevelItem(item)
            if len(self.items) > 200:
                old = self.list.takeTopLevelItem(0).data(0, Qt.UserRole)
                self.items.pop(old, None)
                self.records.pop(old, None)
            self.list.setCurrentItem(item)
        elif kind == "tool.finished":
            key = f"{self.epoch}:{payload['id']}"
            item = self.items.get(key)
            if item is None:
                return
            result = payload["result"]
            self.records[key] = result
            item.setText(1, "完成" if result.get("status") == "ok" else "未完成")
            item.setText(2, result.get("summary", ""))
            if self.list.currentItem() is item:
                self.select(item)
        elif kind.startswith("task.") and kind != "task.started":
            for key, item in self.items.items():
                if key not in self.records:
                    item.setText(1, "已中断 / 待核对")

    def select(self, item, previous=None):
        self._receipt.clear()
        self._select_generation += 1
        generation = self._select_generation
        self.summary.clear()
        self.fields.clear()
        self.svg_preview.load(QByteArray())
        self.preview_notice.setText("此记录暂无原理图预览。")
        if item is None:
            return
        result = self.records.get(item.data(0, Qt.UserRole))
        if result is None:
            return
        self.summary.setPlainText("正在读取完整结果…")
        self._busy.set(True)

        def failed(_exc):
            self._render_prepared(generation, {
                "notice": "完整结果暂不可读；请稍后重试。", "fields": "",
            })

        try:
            future = self.source(
                result,
                context=item.data(0, Qt.UserRole + 1) in {"get_context", "get_entry_context"},
            )
        except (ValueError, RuntimeError) as exc:
            failed(exc)
            return
        self._receipt.watch(
            future,
            lambda prepared: self._render_prepared(generation, prepared),
            failed,
        )

    def _render_prepared(self, generation, prepared):
        if generation != self._select_generation:
            return
        self._busy.set(False)
        if prepared.get("notice"):
            self.summary.setPlainText(prepared["notice"])
        else:
            self.summary.setHtml(prepared.get("summary", ""))
        self.fields.setHtml(prepared.get("fields", ""))
        images = prepared.get("images", [])
        self.preview_notice.setText(prepared.get("preview_notice") or (
            "保存后的原理图；Cadence 参数表达式可能保留原样。"
            if images else "此记录暂无原理图预览。"))
        if images:
            self.svg_preview.load(QByteArray(images[0]))
            if not self.svg_preview.renderer().isValid():
                self.preview_notice.setText("原理图预览无法显示，原始 SVG 文件保留。")
        # 结果表格与数据明细里的表格同样用统一的灰边。
        style_tables(self.summary.document())
        style_tables(self.fields.document())

    def closeEvent(self, event):
        self._receipt.clear()
        super().closeEvent(event)
