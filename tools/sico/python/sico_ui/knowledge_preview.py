"""Asynchronous knowledge index and citation preview bound to the displayed draft."""

from copy import deepcopy
import uuid

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from sico.service.published import thaw
from .chrome import SiDialog
from .receipts import DataReceipt

STATUS = {"path_changed": "路径已变化", "busy": "正在构建", "dependency_missing": "缺少依赖",
          "cleanup_unconfirmed": "进程清理未确认", "ready": "就绪", "pending_index": "待索引", "unchecked": "待检索验证", "disabled": "未启用",
          "stale": "索引过期", "partial": "部分可用", "auth_required": "需认证", "unavailable": "不可用",
          "timeout": "超时", "cancelled": "已取消", "ocr_required": "需 OCR", "stale_reference": "引用失效"}


class KnowledgePreview(SiDialog):
    def __init__(self, parent, manifest):
        super().__init__("知识源检索预览", parent)
        self.settings, self.manifest = parent, deepcopy(manifest)
        self.mode = parent.ui.mode.currentData()
        self.receipt, self.active, self.identity = DataReceipt(self), False, None
        self.cancel_requested, self.closed = False, False
        self.resize(860, 680)
        layout = QVBoxLayout()
        self.content_layout().addLayout(layout)
        self.message = QLabel("使用当前显示配置（含未保存草稿）；操作不保存工程默认。")
        self.message.setTextFormat(Qt.PlainText)
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        bar = QHBoxLayout()
        self.source = QComboBox()
        for row in manifest["knowledge_sources"]:
            if row["enabled"]:
                self.source.addItem(row["name"], row)
        bar.addWidget(self.source, 1)
        self.build = QPushButton("更新选中本地索引")
        self.build.clicked.connect(lambda _checked=False: self.build_source())
        bar.addWidget(self.build)
        self.check = QPushButton("检查状态")
        self.check.clicked.connect(lambda _checked=False: self.start("status", {}))
        bar.addWidget(self.check)
        layout.addLayout(bar)
        bar = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText("输入问题，联合检索全部启用的知识源")
        self.query.setMaxLength(1024)
        self.search = QPushButton("搜索")
        self.search.clicked.connect(lambda _checked=False: self.start("search", {"query": self.query.text(), "limit": 8}))
        self.query.returnPressed.connect(lambda: self.search.click())
        bar.addWidget(self.query, 1)
        bar.addWidget(self.search)
        layout.addLayout(bar)
        self.rows = QTreeWidget()
        self.rows.setHeaderLabels(["来源 / 标题", "状态 / 片段", "定位"])
        self.rows.setColumnWidth(0, 220)
        self.rows.setColumnWidth(1, 340)
        layout.addWidget(self.rows, 1)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setMaximumHeight(180)
        layout.addWidget(self.output)
        actions = QHBoxLayout()
        self.read = QPushButton("核验并读取选中原文")
        self.read.clicked.connect(lambda _checked=False: self.read_source())
        self.cancel = QPushButton("取消操作")
        self.cancel.clicked.connect(lambda _checked=False: self.stop())
        close = QPushButton("关闭")
        close.clicked.connect(lambda _checked=False: self.reject())
        for button in (self.read, self.cancel, close):
            actions.addWidget(button)
        layout.addLayout(actions)
        self.rows.itemSelectionChanged.connect(self.refresh_controls)
        self.source.currentIndexChanged.connect(lambda _index: self.refresh_controls())
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.check_scope)
        self.timer.start(200)
        self.finished.connect(lambda _result: self.cleanup())
        self.start("status", {})

    def current(self):
        return (not self.closed and self.settings.current() and self.settings.ui.mode.currentData() == self.mode
                and self.settings.displayed_manifest() == self.manifest)

    def refresh_controls(self):
        ready = self.current() and not self.active
        row = self.source.currentData() or {}
        self.build.setEnabled(ready and row.get("type") == "local")
        for button in (self.check, self.search):
            button.setEnabled(ready)
        self.cancel.setEnabled(self.active and not self.cancel_requested)
        item = self.rows.currentItem()
        self.read.setEnabled(ready and item is not None and bool(item.data(0, Qt.UserRole)))

    def check_scope(self):
        if not self.current():
            self.stop()
            self.message.setText("页面或配置已改变，请关闭后重新打开预览；已完成的索引更新会保留。")
            self.refresh_controls()

    def build_source(self):
        row = self.source.currentData()
        if row and row["type"] == "local":
            self.start("build", {"source_id": row["id"]})

    def read_source(self):
        item = self.rows.currentItem()
        row = item.data(0, Qt.UserRole) if item else None
        if row:
            self.start("read", {"source_id": row["source_id"], "evidence_id": row["evidence_id"]})

    def start(self, action, arguments):
        if self.active or not self.current():
            return
        self.active, self.cancel_requested = True, False
        self.identity, self.action = uuid.uuid4().hex, action
        self.refresh_controls()
        self.message.setText("正在更新索引，失败或取消时保留上一可用版本…" if action == "build" else "正在检查资料与引用…")
        try:
            future = self.settings.api.agent_settings.knowledge(self.identity, self.manifest,
                        action, arguments, self.settings.token)
            self.receipt.watch(future, self.loaded, self.failed)
        except (ValueError, RuntimeError, OSError) as exc:
            self.failed(exc)

    def loaded(self, value):
        self.active = False
        if not self.current():
            self.check_scope()
            return
        if self.cancel_requested:
            self.message.setText("操作已结束；取消可能发生在索引切换之后，请检查状态。")
            self.refresh_controls()
            return
        if value["status"] != "checked":
            self.message.setText(value.get("message", "检查未完成"))
            self.refresh_controls()
            return
        result = thaw(value["result"])
        if self.action == "read":
            location = result.get("location", {})
            locator = ("页码：" + str(location["page"]) if location.get("page") else
                       "行号：" + str(location["start"]) + "–" + str(location.get("end", location["start"]))
                       if location.get("start") else "")
            self.output.setPlainText((result.get("uri", "") + "\n" + locator + "\n版本：" + result.get("version", "") +
                "\n" + result.get("text", result.get("message", "引用未能核验"))).strip())
        else:
            self.rows.clear()
            self.output.clear()
            for row in result.get("sources", [result] if self.action == "build" else []):
                status = STATUS.get(row["status"], row["status"])
                details = row.get("message", "")
                if "documents" in row:
                    details += f" {row['documents']} 文件 / {row['chunks']} 片段"
                self.rows.addTopLevelItem(QTreeWidgetItem([row.get("source_id", ""), status, details]))
                for issue in row.get("issues", [])[:8]:
                    self.rows.addTopLevelItem(QTreeWidgetItem([issue.get("document", ""), "需 OCR", str(issue.get("page", ""))]))
            for row in result.get("hits", []):
                item = QTreeWidgetItem([row["source_id"] + " / " + row["title"], row["excerpt"], row["uri"]])
                item.setData(0, Qt.UserRole, row)
                item.setToolTip(1, row["excerpt"])
                item.setToolTip(2, row["uri"] + "\n版本：" + row["version"])
                self.rows.addTopLevelItem(item)
        status = result.get("status", "ready")
        message = ("没有匹配结果。" if result.get("no_matches") and status == "ready" else
                   "操作已完成。" if status == "ready" else
                   STATUS.get(status, status) + "：" + result.get("message", "请查看各来源状态。"))
        self.message.setText(message + "原文只用于解释与查证，PDK/CDF 仍以结构化接口为准。")
        self.refresh_controls()

    def failed(self, _error):
        self.active = False
        self.message.setText("回执未确认；请检查状态后重试，工程默认和原文未修改。")
        self.refresh_controls()

    def stop(self):
        if self.active and not self.cancel_requested:
            self.cancel_requested = True
            try:
                self.settings.api.agent_settings.cancel_knowledge(self.identity)
            except (ValueError, OSError, RuntimeError):
                pass
            self.message.setText("已请求取消，正在回收检查进程…")

    def cleanup(self):
        self.stop()
        self.closed = True
        self.timer.stop()
        self.receipt.clear()
