"""One dialog for native thread history and review operations."""

from PyQt5.QtWidgets import QCheckBox, QComboBox, QFormLayout, QLabel, QLineEdit, QSpinBox

from .thread_control_base import ThreadControlDialog


class HistoryDialog(ThreadControlDialog):
    def __init__(self, window):
        super().__init__(window, "会话历史与操作")

    def build(self):
        self.fork = self.button(self.body, "创建并切换分支", lambda: {"kind": "fork"})
        self.compact = self.button(self.body, "压缩会话", lambda: {"kind": "compact"})
        form = QFormLayout()
        self.turns = QSpinBox()
        self.turns.setRange(1, 1000)
        form.addRow("回退回合数", self.turns)
        self.body.addLayout(form)
        self.history_only = QCheckBox("仅回退会话历史，文件与 OA 更改保持原状")
        self.body.addWidget(self.history_only)
        self.rollback = self.button(self.body, "回退历史", lambda: {"kind": "rollback",
            "num_turns": self.turns.value(), "history_only": self.history_only.isChecked()})
        self.rollback_hint = QLabel()
        self.rollback_hint.setWordWrap(True)
        self.body.addWidget(self.rollback_hint)
        self.review_type = QComboBox()
        for label, value in (("未提交更改", "uncommittedChanges"), ("基准分支", "baseBranch"),
                             ("指定提交", "commit"), ("自定义审阅", "custom")):
            self.review_type.addItem(label, value)
        self.review_value = QLineEdit()
        form = QFormLayout()
        form.addRow("审阅范围", self.review_type)
        form.addRow("分支 / 提交 / 要求", self.review_value)
        self.body.addLayout(form)
        self.review = self.button(self.body, "开始代码审阅", self.review_action)
        self.body.addStretch(1)

    def review_action(self):
        kind, value = self.review_type.currentData(), self.review_value.text()
        target = {"type": kind}
        if kind == "commit":
            target.update(sha=value, title=None)
        elif kind == "baseBranch":
            target["branch"] = value
        elif kind == "custom":
            target["instructions"] = value
        return {"kind": "review", "target": target}

    def render_page(self, snapshot, _initial):
        paginated = snapshot.get("history_mode") == "paginated"
        self.summary.setText("分页会话历史" if paginated else "旧格式会话历史")
        available = not snapshot.get("busy", False) and bool(snapshot.get("thread_id"))
        for button in self.buttons:
            button.setEnabled(available)
        self.rollback.setEnabled(available and paginated)
        self.rollback_hint.setText("仅回退会话历史，工作区文件不变。" if paginated else
                                   "旧格式会话不支持历史回退；历史保留，可继续会话。")
