"""One dialog for the explicit goal of the current thread."""

from PyQt5.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QHBoxLayout,
    QPlainTextEdit,
    QSpinBox,
    QStyle,
    QToolButton,
    QWidget,
)

from .form_styles import accent_field_stylesheet, checkbox_stylesheet
from .thread_control_base import ThreadControlDialog

STATUSES = {"active": "执行中", "paused": "已暂停", "blocked": "等待补充",
            "usageLimited": "用量受限", "budgetLimited": "预算已用完", "complete": "已完成"}


class GoalDialog(ThreadControlDialog):
    def __init__(self, window):
        super().__init__(window, "持续目标")

    def build(self):
        self.objective = QPlainTextEdit()
        self.objective.setPlaceholderText("目标")
        self.body.addWidget(self.objective, 1)
        form = QFormLayout()
        self.budget = QSpinBox()
        self.budget.setRange(0, 2147483647)
        self.budget.setSpecialValueText("未设置")
        self.budget.setToolTip("回合结算时检查，单个回合可能超过预算")
        self.budget.setStyleSheet(accent_field_stylesheet())
        self.unlimited = QCheckBox("不限制词元预算")
        self.unlimited.setStyleSheet(checkbox_stylesheet())
        self.unlimited.toggled.connect(lambda enabled: self.budget.setEnabled(not enabled))
        budget_row = QWidget()
        budget_layout = QHBoxLayout(budget_row)
        budget_layout.setContentsMargins(0, 0, 0, 0)
        budget_layout.addWidget(self.budget, 1)
        budget_layout.addWidget(self.unlimited)
        form.addRow("词元预算", budget_row)
        self.body.addLayout(form)
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
        self.buttons.append(self.pause)
        self.clear = self.button(actions, "清除目标", lambda: {"kind": "goal_clear"})
        self.body.addLayout(actions)

    def render_page(self, snapshot, initial):
        goal = snapshot.get("goal")
        self.summary.setText((STATUSES.get(goal["status"], goal["status"]) + " | " +
            str(goal["tokensUsed"]) + " / " +
            ("不限额" if goal["tokenBudget"] is None else str(goal["tokenBudget"])) +
            " tokens | " + str(goal["timeUsedSeconds"]) + " s")
            if goal else "当前没有持续目标")
        self.summary.setToolTip(goal["objective"] if goal else "")
        available = not snapshot.get("busy", False) and bool(snapshot.get("thread_id"))
        for button in self.buttons:
            button.setEnabled(available or button is self.start)
        self.pause.setEnabled(bool(goal and goal["status"] == "active"))
        if initial and goal:
            self.objective.setPlainText(goal["objective"])
            self.budget.setValue(min(2147483647, goal["tokenBudget"] or 0))
            self.unlimited.setChecked(goal["tokenBudget"] is None)
