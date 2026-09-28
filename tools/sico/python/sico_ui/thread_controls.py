"""Explicit goal, history and memory commands for one captured session."""

import uuid

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QStyle,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .chrome import SiDialog
from .receipts import DataReceipt

STATUSES = {"active": "执行中", "paused": "已暂停", "blocked": "等待补充",
            "usageLimited": "用量受限", "budgetLimited": "预算已用完", "complete": "已完成"}


class ThreadDialog(SiDialog):
    def __init__(self, window):
        super().__init__("持续目标与会话操作", window)
        self.window = window
        self.frontend = window.api
        self.controller = window.page.session
        self.closed = False
        self.activation, self.context = window.page.activation, window.presentation.displayed_context
        self.scope, self.snapshot = None, {}
        self.receipt = DataReceipt(self)
        self.resize(640, 520)
        self.setMinimumSize(440, 430)
        layout = self.content_layout()
        layout.setContentsMargins(12, 10, 12, 12)
        self.summary, self.hint = QLabel(), QLabel()
        for label in (self.summary, self.hint):
            label.setTextFormat(Qt.PlainText)
            label.setWordWrap(True)
            label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            layout.addWidget(label)
        self.summary.setMaximumHeight(64)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.commands = []
        self._goal_page()
        self._history_page()
        self._memory_page()
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.reload)
        self.timer.start()
        self.reload()

    def page(self, title):
        page = QWidget()
        layout = QVBoxLayout(page)
        self.tabs.addTab(page, title)
        return layout

    def button(self, layout, text, action):
        button = QPushButton(text)
        button.clicked.connect(lambda _checked=False: self.send(action()))
        layout.addWidget(button)
        self.commands.append(button)
        return button

    def _goal_page(self):
        layout = self.page("持续目标")
        self.objective = QPlainTextEdit()
        self.objective.setPlaceholderText("目标")
        layout.addWidget(self.objective, 1)
        form = QFormLayout()
        self.budget = QSpinBox()
        self.budget.setRange(0, 2147483647)
        self.budget.setSpecialValueText("未设置")
        self.budget.setToolTip("回合结算时检查，单个回合可能超过预算")
        form.addRow("Token 预算", self.budget)
        self.unlimited = QCheckBox("不限制 Token 预算")
        self.unlimited.toggled.connect(lambda enabled: self.budget.setEnabled(not enabled))
        form.addRow("", self.unlimited)
        layout.addLayout(form)
        actions = QHBoxLayout()
        self.start = self.button(actions, "启动目标", lambda: {"kind": "goal_start",
            "objective": self.objective.toPlainText(),
            "token_budget": None if self.unlimited.isChecked() else self.budget.value()})
        self.pause = QToolButton()
        self.pause.setIcon(self.style().standardIcon(QStyle.SP_MediaPause))
        self.pause.setToolTip("暂停目标与当前生成")
        self.pause.setFixedSize(30, 30)
        self.pause.clicked.connect(lambda: self.send({"kind": "goal_pause"}))
        actions.addWidget(self.pause)
        self.button(actions, "清除目标", lambda: {"kind": "goal_clear"})
        layout.addLayout(actions)

    def _history_page(self):
        layout = self.page("会话历史")
        self.button(layout, "创建并切换分支", lambda: {"kind": "fork"})
        self.button(layout, "压缩会话", lambda: {"kind": "compact"})
        form = QFormLayout()
        self.turns = QSpinBox()
        self.turns.setRange(1, 1000)
        form.addRow("回退回合数", self.turns)
        layout.addLayout(form)
        self.history_only = QCheckBox("仅回退会话历史，文件与 OA 更改保持原状")
        layout.addWidget(self.history_only)
        self.rollback = self.button(layout, "回退历史", lambda: {"kind": "rollback",
            "num_turns": self.turns.value(), "history_only": self.history_only.isChecked()})
        self.rollback_hint = QLabel()
        self.rollback_hint.setWordWrap(True)
        layout.addWidget(self.rollback_hint)
        self.review_type = QComboBox()
        for label, value in (("未提交更改", "uncommittedChanges"), ("基准分支", "baseBranch"),
                             ("指定提交", "commit"), ("自定义审阅", "custom")):
            self.review_type.addItem(label, value)
        self.review_value = QLineEdit()
        form = QFormLayout()
        form.addRow("审阅范围", self.review_type)
        form.addRow("分支 / 提交 / 要求", self.review_value)
        layout.addLayout(form)
        self.button(layout, "开始审阅", self.review_action)
        layout.addStretch(1)

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

    def _memory_page(self):
        layout = self.page("会话记忆")
        self.memory_flags = {}
        for key, text in (("enabled", "启用会话记忆"), ("generate", "生成记忆"),
                          ("use", "使用记忆")):
            flag = QCheckBox(text)
            self.memory_flags[key] = flag
            layout.addWidget(flag)
        self.memory_flags["enabled"].toggled.connect(self.memory_enabled)
        self.memory_enabled(False)
        self.memory_state = QLabel("尚未读取")
        self.memory_state.setWordWrap(True)
        layout.addWidget(self.memory_state)
        note = QLabel("记忆保存在当前 SiCo 会话内，分支共享此范围。启用后可能产生额外模型用量。")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.button(layout, "应用记忆设置", lambda: {"kind": "memory", "settings": {
            key: flag.isChecked() for key, flag in self.memory_flags.items()}})
        layout.addStretch(1)

    def memory_enabled(self, enabled):
        for key in ("generate", "use"):
            self.memory_flags[key].setEnabled(enabled)
            if not enabled:
                self.memory_flags[key].setChecked(False)

    def current(self):
        return (not self.closed and self.window.page.session == self.controller
                and self.frontend.owns(self.controller)
                and self.window.page.activation == self.activation
                and self.window.presentation.displayed_context == self.context
                and self.window.page.reviewing is None and not self.window.lifecycle.closing)

    def command(self, name, *args, **kwargs):
        return self.frontend.command(self.controller, name, *args, **kwargs)

    def reload(self):
        if self.receipt.pending is not None:
            return
        if not self.current():
            self.fail(ValueError("会话或来源已变化，请重新打开操作窗口"))
            self.timer.stop()
            return
        try:
            future = self.command("thread_status")
            self.receipt.watch(future, self.render, self.fail)
        except (ValueError, RuntimeError) as exc:
            self.fail(exc)

    def render(self, snapshot):
        if not self.current():
            return
        initial = self.scope is None
        self.scope, self.snapshot = snapshot["scope"], snapshot
        goal = snapshot.get("goal")
        self.summary.setText((STATUSES.get(goal["status"], goal["status"]) + " | " +
            str(goal["tokensUsed"]) + " / " + (
                "不限额" if goal["tokenBudget"] is None else str(goal["tokenBudget"])) +
            " tokens | " + str(goal["timeUsedSeconds"]) + " s")
            if goal else "当前没有持续目标")
        self.summary.setToolTip(goal["objective"] if goal else "")
        operation = snapshot.get("operation")
        if operation:
            self.hint.setText({"executing": "操作执行中", "completed": "操作已完成",
                "cancelled": "操作已停止", "failed": "操作失败",
                "needs_reconcile": "操作结果待核对，不会自动重发"}.get(
                    operation["status"], operation["status"]))
        busy = snapshot.get("busy", False)
        for button in self.commands:
            available = button is self.start or bool(snapshot.get("thread_id"))
            button.setEnabled(not busy and available)
        self.pause.setEnabled(bool(goal and goal["status"] == "active"))
        paginated = snapshot.get("history_mode") == "paginated"
        self.rollback.setEnabled(not busy and paginated and bool(snapshot.get("thread_id")))
        self.rollback_hint.setText("仅回退会话历史，工作区文件不变。" if paginated else
                                   "旧格式会话不支持历史回退；历史保留，可继续会话。")
        memory = snapshot.get("memory", {})
        effective = memory.get("effective")
        self.memory_state.setText("实际配置：" + ("，".join(
            label + ("已启用" if effective.get(key) else "已关闭")
            for key, label in (("enabled", "记忆"), ("generate", "生成"), ("use", "使用")))
            if effective is not None and memory.get("verified") else "尚未验证"))
        if initial:
            if goal:
                self.objective.setPlainText(goal["objective"])
                self.budget.setValue(min(2147483647, goal["tokenBudget"] or 0))
                self.unlimited.setChecked(goal["tokenBudget"] is None)
            for key, flag in self.memory_flags.items():
                flag.setChecked((memory.get("configured") or effective or {}).get(key) is True)

    def send(self, action):
        if not self.current() or self.scope is None:
            self.fail(ValueError("会话或来源已变化，操作未发送"))
            return
        self.receipt.clear()
        for button in self.commands + [self.pause]:
            button.setEnabled(False)
        try:
            future = self.command("thread_operation",
                action, scope=self.scope, request_id=uuid.uuid4().hex)
            self.receipt.watch(future, self.accepted, self.fail)
        except (ValueError, RuntimeError) as exc:
            self.fail(exc)

    def accepted(self, _request):
        if self.current():
            self.hint.setText("操作已接收")
            self.reload()

    def fail(self, exc):
        if self.current():
            self.hint.setText(str(exc))
        else:
            self.hint.setText("会话或来源已变化，请重新打开操作窗口")
        for button in self.commands + [self.pause]:
            button.setEnabled(False)

    def done(self, result):
        self.closed = True
        self.timer.stop()
        self.receipt.clear()
        super().done(result)

    def closeEvent(self, event):
        self.closed = True
        self.timer.stop()
        self.receipt.clear()
        super().closeEvent(event)


def show_thread_controls(window):
    if window.page.reviewing is not None or window.page.opening:
        window.info_app.notice("无法操作会话", "请先进入要操作的会话")
        return
    previous = getattr(window, "thread_dialog", None)
    if previous is not None:
        previous.close()
        previous.deleteLater()
    window.thread_dialog = ThreadDialog(window)
    window.thread_dialog.show()
