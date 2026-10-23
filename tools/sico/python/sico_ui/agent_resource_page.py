"""One resource-category list and its staged editing controls."""

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QHBoxLayout, QHeaderView, QLabel, QPushButton, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from cadai.mcp_server import KNOWLEDGE_ID

from .agent_resource_editor import LABELS
from .agent_resource_status import SOURCE, STATUS

KINDS = {**LABELS, "knowledge_sources": "知识源", "mcp_servers": "MCP 服务",
         "skill_overrides": "单技能设置"}
BUILTIN_LABELS = {
    KNOWLEDGE_ID: ("派生 MCP", "由知识源管理", "只提供 search/read；连接与凭据在知识源中维护"),
}
BUILTIN_FALLBACK = ("内置 MCP", "由 SiCo 管理", "通过当前会话的平台连接提供；此处不重复配置")

# 添加按钮的中文悬停说明：技能（发现面）与能力（线程绑定面）的差异最容易被混淆。
ADD_TIPS = {
    "skill_roots": "技能目录：存放含 SKILL.md 的技能。\n"
                   "Codex 会发现并列入会话，模型按需加载使用。",
    "capability_roots": "能力目录：绑定到当前会话线程、按需读取的外部目录。\n"
                        "内容不默认注入模型；线程建立后固定，换目录需新建会话。",
}


class ResourcePage(QWidget):
    addRequested = pyqtSignal(str)
    editRequested = pyqtSignal(str, int)
    toggleRequested = pyqtSignal(str, int)
    removeRequested = pyqtSignal(str, int)

    def __init__(self, kinds, hint, additions, parent=None):
        super().__init__(parent)
        self.kinds, self.editable = kinds, False
        layout = QVBoxLayout(self)
        description = QLabel(hint)
        description.setWordWrap(True)
        description.setTextFormat(Qt.PlainText)
        layout.addWidget(description)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["资源 / 路径", "类型", "状态"])
        self.tree.setRootIsDecorated(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setMinimumHeight(160)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.tree.itemSelectionChanged.connect(self.selection_changed)
        layout.addWidget(self.tree, 1)
        self.empty = QLabel("尚未配置资源")
        layout.addWidget(self.empty)
        actions = QHBoxLayout()
        self.add_buttons = []
        for title, kind, available in additions:
            button = QPushButton(title)
            button.setEnabled(False)
            button.setToolTip(ADD_TIPS.get(kind) if available else
                              "此连接方式尚未接通，当前版本不能保存或加载")
            button.clicked.connect(lambda _checked=False, k=kind: self.addRequested.emit(k))
            actions.addWidget(button)
            self.add_buttons.append((button, available))
        actions.addStretch()
        layout.addLayout(actions)
        actions = QHBoxLayout()
        actions.addStretch()
        self.edit, self.toggle, self.remove = (QPushButton(text) for text in ("编辑", "启用 / 禁用", "移除"))
        for button, signal in ((self.edit, self.editRequested), (self.toggle, self.toggleRequested),
                               (self.remove, self.removeRequested)):
            button.clicked.connect(lambda _checked=False, s=signal: self.request(s))
            actions.addWidget(button)
        layout.addLayout(actions)
        self.selection_changed()

    def request(self, signal):
        item = self.tree.currentItem()
        if item is not None and self.editable:
            value = item.data(0, Qt.UserRole)
            if value is not None:
                signal.emit(*value)

    def selection_changed(self):
        item = self.tree.currentItem()
        value = item.data(0, Qt.UserRole) if item is not None else None
        available = self.editable and value is not None and value[0] in {
            *LABELS, "mcp_servers", "skill_overrides", "knowledge_sources"}
        for button in (self.edit, self.toggle, self.remove):
            button.setEnabled(available)

    def render(self, manifest, *, editable=False, snapshot=None):
        self.editable = editable
        self.tree.clear()
        snapshot = snapshot or {}
        statuses = {(r["kind"], r["id"]): r for r in snapshot.get("rows", [])}
        statuses.update({("mcp_servers", r["id"]): r for r in snapshot.get("mcp_servers", [])})
        statuses.update({("knowledge_sources", r["id"]): r for r in snapshot.get("knowledge_sources", [])})
        if "mcp_servers" in self.kinds:
            for row in snapshot.get("builtin_mcp", []):
                kind, owner, tip = BUILTIN_LABELS.get(row.get("id"), BUILTIN_FALLBACK)
                item = QTreeWidgetItem([row.get("id", ""), kind, owner])
                item.setToolTip(0, tip)
                self.tree.addTopLevelItem(item)
        for kind in self.kinds:
            for index, row in enumerate(manifest.get(kind, [])):
                name = row.get("name") or row.get("id") or row["path"]
                location = row.get("path") or row.get("url") or row.get("command", "")
                status = statuses.get((kind, row.get("id")), {})
                label = STATUS.get(status.get("status"), "") or (
                    "启用" if row["enabled"] else "未启用")
                message = str(status.get("message") or "")
                source = SOURCE.get(status.get("source") or row.get("source"), "")
                item = QTreeWidgetItem([name + ("\n" + location if location != name else ""),
                                       KINDS[kind] + (" · " + source if source else ""),
                                       label + (" · " + message if message and message != label else "")])
                item.setData(0, Qt.UserRole, (kind, index))
                item.setToolTip(0, location)
                item.setToolTip(2, message or label)
                self.tree.addTopLevelItem(item)
                if status.get("access"):
                    QTreeWidgetItem(item, ["使用范围", "", "读取" if status["access"] == "read"
                                           else "加载和调用"])
                for server in status.get("servers", []):
                    state = {"connected": "已连接", "failed": "失败", "starting": "正在启动"}.get(
                        server.get("status"), "未验证")
                    QTreeWidgetItem(item, [server["name"], "插件 MCP", state + " / " +
                                          str(server.get("tools", 0)) + " 个工具"])
                item.setExpanded(True)
        if "skill_roots" in self.kinds and snapshot.get("skills"):
            group = QTreeWidgetItem(["会话已发现的技能", "只读", str(len(snapshot["skills"]))])
            self.tree.addTopLevelItem(group)
            for skill in snapshot["skills"]:
                child = QTreeWidgetItem(group, [skill["name"], "Codex 技能",
                                                "已启用" if skill["enabled"] else "未启用"])
                child.setToolTip(0, skill["path"])
            group.setExpanded(True)
        if "skill_roots" in self.kinds:
            for dep in snapshot.get("dependencies", []):
                item = QTreeWidgetItem([str(dep["skill"]) + " → " + str(dep.get("name") or "未指定"),
                                       "技能依赖", STATUS.get(dep["status"], "未验证")])
                self.tree.addTopLevelItem(item)
        self.empty.setVisible(self.tree.topLevelItemCount() == 0)
        for button, available in self.add_buttons:
            button.setEnabled(editable and available)
        self.selection_changed()
