"""Worker-backed external resource status for one captured live session."""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QStyle,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from .chrome import SiDialog
from .receipts import DataReceipt

STATUS = {"pending": "待检查", "registered": "已注册", "available": "可用",
          "selected": "已选择", "unavailable": "不可用", "empty": "未发现技能",
          "dependency_missing": "依赖未就绪", "missing": "未连接", "unverified": "未验证"}
KINDS = {"skill_roots": "技能目录", "plugins": "插件", "capability_roots": "能力目录"}


class ResourceDialog(SiDialog):
    def __init__(self, window):
        super().__init__("外部技能与插件", window)
        self.window, self.controller = window, window.page.session
        self.scope = window.actions.capture()
        self.activation = window.page.activation
        self.receipt = DataReceipt(self)
        self.resize(780, 550)
        column = QVBoxLayout()
        self.content_layout().addLayout(column)
        self.caption = QLabel("会话：" + window.api.display_name(self.controller))
        self.caption.setTextFormat(Qt.PlainText)
        self.caption.setWordWrap(True)
        column.addWidget(self.caption)
        self.hint = QLabel()
        self.hint.setTextFormat(Qt.PlainText)
        self.hint.setWordWrap(True)
        column.addWidget(self.hint)
        self.rows = QTreeWidget()
        self.rows.setHeaderLabels(["资源", "来源 / 状态", "说明"])
        self.rows.setWordWrap(True)
        self.rows.setColumnWidth(0, 180)
        self.rows.setColumnWidth(1, 155)
        column.addWidget(self.rows, 1)
        actions = QHBoxLayout()
        self.refresh = QToolButton()
        self.refresh.setIcon(self.style().standardIcon(QStyle.SP_BrowserReload))
        self.refresh.setToolTip("重新检查")
        self.refresh.setFixedSize(28, 28)
        self.refresh.clicked.connect(self.reload)
        actions.addWidget(self.refresh)
        actions.addStretch(1)
        close = QToolButton()
        close.setIcon(self.style().standardIcon(QStyle.SP_DialogCloseButton))
        close.setToolTip("关闭")
        close.setFixedSize(28, 28)
        close.clicked.connect(lambda _checked=False: self.close())
        actions.addWidget(close)
        column.addLayout(actions)
        self.reload()

    def current(self):
        return self.window.actions.current(self.scope) and self.window.page.reviewing is None

    def reload(self, _checked=False):
        if not self.current():
            self.fail(ValueError("会话已切换，请在当前会话重新打开资源窗口"))
            return
        self.refresh.setEnabled(False)
        self.hint.setText("正在检查外部资源…")
        try:
            future = self.window.api.command(self.controller, "resource_status")
            self.receipt.watch(future, self.render, self.fail)
        except (ValueError, RuntimeError) as exc:
            self.fail(exc)

    def fail(self, exc):
        current = self.current()
        self.refresh.setEnabled(current)
        self.hint.setText(str(exc) if current else "会话已切换，请在当前会话重新打开资源窗口")

    def render(self, snapshot):
        if not self.current():
            self.fail(ValueError("会话已切换，未显示旧会话的检查结果"))
            return
        self.refresh.setEnabled(True)
        self.rows.clear()
        messages = list(snapshot.get("issues", []))
        if snapshot.get("busy"):
            messages.append("任务执行中 · 最近检查结果")
        elif snapshot.get("configured") and snapshot.get("status") in {"pending", "not_started"}:
            messages.append("等待会话进程启动")
        if not snapshot.get("configured"):
            messages.append("尚未配置外部资源")
        if snapshot.get("manifest"):
            messages.append("清单：" + snapshot["manifest"])
        self.hint.setText("\n".join(messages))
        for row in snapshot.get("rows", []):
            name = row.get("name", row["id"])
            source = {"project": "项目", "user": "用户"}.get(row.get("source"), "")
            item = QTreeWidgetItem([KINDS.get(row["kind"], row["kind"]) + " · " + name,
                                    source + " / " + STATUS.get(row["status"], row["status"]),
                                    row.get("message", "")])
            self.rows.addTopLevelItem(item)
            for index in range(3):
                item.setToolTip(index, item.text(index))
            path_item = QTreeWidgetItem(item, ["目录", "", row["path"]])
            path_item.setToolTip(2, row["path"])
            QTreeWidgetItem(item, ["使用范围", "", "读取" if row["access"] == "read" else
                                   "加载和调用"])
            for server in row.get("servers", []):
                QTreeWidgetItem(item, [server["name"], str(server.get("status") or "未验证"),
                                       "工具数：" + str(server["tools"])])
            item.setExpanded(True)
        if snapshot.get("skills"):
            group = QTreeWidgetItem(["已发现技能", str(len(snapshot["skills"])), ""])
            self.rows.addTopLevelItem(group)
            for skill in snapshot["skills"]:
                child = QTreeWidgetItem(group, [skill["name"],
                    "已启用" if skill["enabled"] else "已禁用", skill.get("pluginId") or ""])
                child.setToolTip(0, skill["path"])
        for dep in snapshot.get("dependencies", []):
            self.rows.addTopLevelItem(QTreeWidgetItem([
                "技能依赖 · " + str(dep["skill"]), STATUS.get(dep["status"], dep["status"]),
                str(dep.get("name") or "未指定"),
            ]))

    def closeEvent(self, event):
        self.receipt.clear()
        super().closeEvent(event)


def show_resources(window):
    if window.page.reviewing is not None or window.page.opening:
        window.info_app.notice("无法检查外部资源", "请先进入要检查的会话")
        return
    if not window.page.view.resources:
        window.info_app.notice("无法检查外部资源", "当前后端不支持 Codex 外部资源")
        return
    previous = getattr(window, "resource_dialog", None)
    if previous is not None:
        previous.close()
        previous.deleteLater()
    window.resource_dialog = ResourceDialog(window)
    window.resource_dialog.show()
