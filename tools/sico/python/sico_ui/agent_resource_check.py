"""An isolated resource-check receipt; cancellation never targets a task runtime."""

from copy import deepcopy
import uuid

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from sico.service.published import thaw
from .agent_resource_status import STATUS
from .chrome import SiDialog
from .receipts import DataReceipt
from .si_prompt import SiConfirm


class ResourceCheckDialog(SiDialog):
    def __init__(self, parent, manifest):
        super().__init__("隔离检查资源", parent)
        self.settings, self.manifest = parent, deepcopy(manifest)
        self.mode = parent.ui.mode.currentData()
        self.receipt, self.identity, self.active = DataReceipt(self), None, False
        self.result, self.confirmation = {}, None
        self.cancel_requested = False
        self.resize(820, 650)
        layout = QVBoxLayout()
        self.content_layout().addLayout(layout)
        self.message = QLabel("检查当前显示配置；不保存、不改变已有会话。")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.rows = QTreeWidget()
        self.rows.setHeaderLabels(["资源", "状态 / 描述", "路径 / 工具"])
        layout.addWidget(self.rows, 1)
        bar = QHBoxLayout()
        self.override = QPushButton("将选中技能启停加入草稿")
        self.override.clicked.connect(lambda _checked=False: self.add_override())
        bar.addWidget(self.override)
        self.preview = QPushButton("预览技能正文")
        self.preview.clicked.connect(lambda _checked=False: self.preview_skill())
        bar.addWidget(self.preview)
        self.check = QPushButton("重新检查")
        self.check.clicked.connect(lambda _checked=False: self.start())
        bar.addWidget(self.check)
        self.cancel = QPushButton("取消检查")
        self.cancel.clicked.connect(lambda _checked=False: self.stop())
        bar.addWidget(self.cancel)
        layout.addLayout(bar)
        callbar = QHBoxLayout()
        self.server, self.tool = QComboBox(), QLineEdit()
        self.tool.setPlaceholderText("已发现的工具名")
        self.call = QPushButton("显式调用…")
        self.call.clicked.connect(lambda _checked=False: self.confirm_call())
        callbar.addWidget(self.server)
        callbar.addWidget(self.tool)
        callbar.addWidget(self.call)
        layout.addLayout(callbar)
        self.arguments = QPlainTextEdit("{}")
        self.arguments.setMaximumHeight(70)
        layout.addWidget(self.arguments)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setMaximumHeight(110)
        layout.addWidget(self.output)
        close = QPushButton("关闭")
        close.clicked.connect(lambda _checked=False: self.reject())
        layout.addWidget(close)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.check_scope)
        self.timer.start(200)
        self.finished.connect(lambda _result: self.cleanup())
        self.start()

    def current(self):
        return (self.settings.current() and self.settings.ui.mode.currentData() == self.mode
                and self.settings.displayed_manifest() == self.manifest)

    def check_scope(self):
        if not self.current():
            self.stop()
            self.message.setText("页面或草稿已变更，请关闭后重新检查。")
            self.refresh_controls()

    def refresh_controls(self):
        ready = self.current() and not self.active
        self.check.setEnabled(ready)
        for button in (self.override, self.preview, self.call):
            button.setEnabled(ready and bool(self.result))
        self.cancel.setEnabled(self.active)

    def start(self, call=None, skill=None):
        if self.active or not self.current():
            return
        self.identity, self.active = uuid.uuid4().hex, True
        self.cancel_requested = False
        self.refresh_controls()
        self.message.setText("正在隔离检查，最长 45 秒；调用工具可能产生其声明的业务效果。" if call else
                             "正在隔离发现技能和 MCP；不会自动调用业务工具。")
        try:
            future = self.settings.api.agent_settings.check(self.identity, self.manifest,
                                                        self.settings.token, call, skill)
            self.receipt.watch(future, self.loaded, self.failed)
        except (ValueError, RuntimeError, OSError) as exc:
            self.failed(exc)

    def loaded(self, value):
        self.active = False
        if not self.current():
            self.check_scope()
            return
        self.result = {}
        self.refresh_controls()
        if value["status"] != "checked":
            self.message.setText(value["message"])
            return
        self.result = thaw(value)
        self.refresh_controls()
        self.rows.clear()
        self.server.clear()
        for row in value["mcp_servers"]:
            item = QTreeWidgetItem([row["id"], STATUS.get(row["status"], row["message"]),
                                   ", ".join(row["tools"])])
            self.rows.addTopLevelItem(item)
            if row["status"] == "available":
                self.server.addItem(row["id"])
        for row in value["skills"]:
            item = QTreeWidgetItem([row["name"], "启用" if row["enabled"] else "未启用",
                                   row.get("definition_path") or row["path"]])
            item.setData(0, Qt.UserRole, thaw(row))
            deps = [str(d["name"]) + "：" + STATUS.get(d["status"], d["status"])
                    for d in value.get("dependencies", []) if d["path"] == row["path"]]
            item.setToolTip(1, row["description"] + ("\n依赖：" + "；".join(deps) if deps else ""))
            item.setToolTip(2, item.text(2))
            self.rows.addTopLevelItem(item)
        metrics = "\n".join(r["name"] + "：" + str(r["count"]) + " 个工具，目录 " +
                            str(r["array_compact_bytes"]) + " B" for r in value.get("mcp_metrics", []))
        self.output.setPlainText(value.get("skill_preview") or value.get("call_result") or metrics)
        self.message.setText("检查已结束，临时进程已清理。启停加入草稿后仍需保存，用于新会话。")

    def failed(self, _error):
        self.active = False
        self.result = {}
        self.message.setText("检查回执未确认；若已提交工具调用，请核对其效果，不要直接重试。")
        self.refresh_controls()

    def add_override(self):
        item = self.rows.currentItem()
        row = item.data(0, Qt.UserRole) if item else None
        if not self.current() or not row or not row.get("definition_path") or not self.settings.can_edit():
            self.message.setText("请选择可编辑工程默认下的外部技能；系统技能没有外部定义路径。")
            return
        path = row["definition_path"]
        index = next((i for i, r in enumerate(self.settings.draft.manifest["skill_overrides"])
                      if r["path"] == path), -1)
        self.settings.draft.replace("skill_overrides", index,
                                 {"path": path, "enabled": not row["enabled"]})
        self.settings.render()
        self.settings.ui.message.setText("单技能启停已加入工程草稿，保存后用于新会话。")
        self.accept()

    def preview_skill(self):
        item = self.rows.currentItem()
        row = item.data(0, Qt.UserRole) if item else None
        if row and row.get("definition_path"):
            self.start(skill={"name": row["name"], "path": row["definition_path"]})
        else:
            self.message.setText("请选择有外部定义路径的技能。")

    def confirm_call(self):
        if not self.current() or self.active or self.confirmation is not None:
            return
        call = {"server": self.server.currentText(), "tool": self.tool.text().strip(),
                "arguments": self.arguments.toPlainText()}
        dialog = self.confirmation = SiConfirm("调用 MCP 工具", "即将调用 " + call["server"] +
            " / " + call["tool"] + "。此操作可能产生工具自身的业务效果，请确认参数。", self)
        dialog.add_choice("call", "调用")
        dialog.add_choice("cancel", "取消", default=True, escape=True)
        dialog.chosen.connect(lambda key: self.start(call) if key == "call" else None)
        dialog.finished.connect(lambda _result: self.clear_confirmation(dialog))
        dialog.open()

    def clear_confirmation(self, dialog):
        self.confirmation = None
        dialog.deleteLater()

    def stop(self):
        if self.active and not self.cancel_requested:
            self.cancel_requested = True
            try:
                self.settings.api.agent_settings.cancel_check(self.identity)
            except (ValueError, RuntimeError, OSError):
                pass
            self.message.setText("已请求取消，等待临时进程清理；已发生的工具效果不会撤销。")

    def cleanup(self):
        self.stop()
        self.receipt.clear()
        self.timer.stop()
        if self.confirmation:
            self.confirmation.reject()
