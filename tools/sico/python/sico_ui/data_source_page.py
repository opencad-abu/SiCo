"""Read-only view and staged editing for project structured-data roots."""

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QHBoxLayout, QHeaderView, QLabel, QPushButton, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from cadai.optional_resources import DATA_SOURCE_ENV
from sico.agent_resources.data_sources import LABELS

STATUS = {"available": "可用", "missing": "路径不存在", "unreadable": "不可读",
          "invalid_type": "类型不符", "invalid": "配置无效", "unconfigured": "未配置"}
SOURCE = {"project": "工程设置", "environment": "服务环境", "session": "会话启动值",
          "none": "未配置"}


class DataSourcePage(QWidget):
    editRequested = pyqtSignal(str)
    clearRequested = pyqtSignal(str)
    saveRequested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.editable = False
        layout = QVBoxLayout(self)
        hint = QLabel(
            "工艺数据和模板数据是结构化只读数据根，不进入知识检索。"
            "工程设置覆盖服务环境默认；保存后用于新会话，已有会话保持启动时的路径。"
        )
        hint.setWordWrap(True)
        hint.setTextFormat(Qt.PlainText)
        layout.addWidget(hint)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["数据源 / 路径", "来源", "状态"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setMinimumHeight(150)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.tree.itemSelectionChanged.connect(self.selection_changed)
        layout.addWidget(self.tree, 1)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setTextFormat(Qt.PlainText)
        layout.addWidget(self.note)
        actions = QHBoxLayout()
        self.edit = QPushButton("编辑…")
        self.clear = QPushButton("清除覆盖")
        for button, signal in ((self.edit, self.editRequested),
                               (self.clear, self.clearRequested)):
            button.clicked.connect(
                lambda _checked=False, s=signal: self.request(s))
            actions.addWidget(button)
        actions.addStretch()
        self.save = QPushButton("保存数据源")
        self.save.setProperty("bottomAction", True)
        self.save.clicked.connect(lambda _checked=False: self.saveRequested.emit())
        actions.addWidget(self.save)
        layout.addLayout(actions)
        self.selection_changed()

    def request(self, signal):
        item = self.tree.currentItem()
        if item is not None and self.editable:
            kind = item.data(0, Qt.UserRole)
            if kind is not None:
                signal.emit(kind)

    def selection_changed(self):
        item = self.tree.currentItem()
        kind = item.data(0, Qt.UserRole) if item is not None else None
        self.edit.setEnabled(bool(self.editable and kind))
        self.clear.setEnabled(bool(self.editable and kind and item.data(1, Qt.UserRole)))

    def render(self, data, *, editable=False, draft=None, mode="project_defaults",
               blocked=False):
        self.editable = bool(editable)
        draft = draft or {}
        self.tree.clear()
        effective = data.get("effective") or {}
        session = data.get("session") or {}
        session_values = session.get("values") if session.get("status") == "available" else {}
        for kind in DATA_SOURCE_ENV:
            project_path = draft[kind] if kind in draft else (data.get("project") or {}).get(kind)
            current = effective.get(kind) or {}
            if mode == "session":
                current = (session_values or {}).get(kind) or current
            path = project_path or current.get("path")
            source = ("工程设置（草稿）" if kind in draft and draft[kind]
                      else "继承服务环境" if kind in draft and draft[kind] is None
                      else SOURCE.get(current.get("source"), "未配置"))
            if mode == "session" and session_values:
                source = "会话启动值"
            if kind in draft and draft[kind] is not None:
                status = "待保存"
            else:
                status = STATUS.get(current.get("status"), "未配置")
                if current.get("message") and current["message"] != status:
                    status += " · " + str(current["message"])[:180]
            item = QTreeWidgetItem([
                LABELS[kind] + ("\n" + str(path) if path else ""),
                source,
                status,
            ])
            item.setData(0, Qt.UserRole, kind)
            item.setData(1, Qt.UserRole, bool(project_path))
            item.setToolTip(0, (DATA_SOURCE_ENV[kind] + "\n" + str(path or "未配置")))
            self.tree.addTopLevelItem(item)
        if data.get("status") == "unavailable":
            self.note.setText(data.get("message") or "数据源设置不可读；当前显示环境默认。")
        elif session.get("status") == "unavailable" and mode == "session":
            self.note.setText(session.get("message", "当前会话未记录数据源路径。"))
        else:
            self.note.setText("清除覆盖会恢复服务环境默认；移除设置在保存前不会改动磁盘。")
        self.save.setEnabled(bool(self.editable and draft and not blocked))
        self.selection_changed()
