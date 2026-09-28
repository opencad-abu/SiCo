"""Session binding controls. All decisions are submitted through the worker API."""

import math
import time
from threading import Event

from PyQt5.QtCore import QSize, Qt, QTimer
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSpinBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .glyph_plates import plate_check_icon
from .glyphs import ACTION_GLYPH_SIZE, cross_icon, refresh_icon


def target_name(record):
    snapshot = record.get("snapshot", {})
    cellview = snapshot.get("cellview") or {}
    name = "/".join(str(cellview.get(key, "")) for key in ("lib", "cell", "view"))
    return name if name.strip("/") else (snapshot.get("ade_session") or "CIW")


class BindingPanel(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.scope = None
        self.state = None
        self.pending_command = False
        self.proposal = None
        self.deadline = 0
        self.latest_result = None
        layout = QVBoxLayout(self)
        self.targets = QTreeWidget()
        self.targets.setHeaderLabels(["绑定目标", "状态"])
        self.targets.header().setStretchLastSection(False)
        self.targets.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.targets.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.targets.setRootIsDecorated(False)
        self.targets.setSelectionMode(QTreeWidget.SingleSelection)
        self.targets.currentItemChanged.connect(self.refresh_controls)
        layout.addWidget(self.targets, 1)
        actions = QHBoxLayout()
        self.select = QPushButton("设为默认")
        self.select.setIcon(plate_check_icon())
        self.release = QPushButton("释放绑定")
        self.release.setIcon(cross_icon(ACTION_GLYPH_SIZE))
        for button in (self.select, self.release):
            button.setIconSize(QSize(ACTION_GLYPH_SIZE, ACTION_GLYPH_SIZE))
        self.select.clicked.connect(lambda: self.target_command("select_binding_target"))
        self.release.clicked.connect(lambda: self.target_command("release_binding"))
        actions.addWidget(self.select)
        actions.addWidget(self.release)
        layout.addLayout(actions)
        recovery = QHBoxLayout()
        self.operations = QComboBox()
        self.operations.setMinimumWidth(0)
        self.operations.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.reconcile = QPushButton()
        self.reconcile.setIcon(refresh_icon(ACTION_GLYPH_SIZE))
        self.reconcile.setIconSize(QSize(ACTION_GLYPH_SIZE, ACTION_GLYPH_SIZE))
        self.reconcile.setToolTip("核对原请求回执")
        self.reconcile.setAccessibleName("核对原请求回执")
        self.reconcile.setFixedSize(30, 30)
        self.reconcile.clicked.connect(self.reconcile_operation)
        recovery.addWidget(self.operations, 1)
        recovery.addWidget(self.reconcile)
        layout.addLayout(recovery)
        self.auto_bind = QCheckBox("超时自动绑定工作流新窗口")
        self.seconds = QSpinBox()
        self.seconds.setRange(1, 300)
        self.seconds.setSuffix(" 秒")
        self.seconds.setValue(30)
        policy = QHBoxLayout()
        policy.addWidget(self.auto_bind, 1)
        policy.addWidget(self.seconds)
        layout.addLayout(policy)
        self.auto_bind.clicked.connect(self.save_policy)
        self.seconds.editingFinished.connect(self.save_policy)
        self.proposal_area = QWidget()
        proposal_layout = QVBoxLayout(self.proposal_area)
        proposal_layout.setContentsMargins(0, 0, 0, 0)
        self.question = QLabel()
        self.question.setTextFormat(Qt.PlainText)
        self.question.setWordWrap(True)
        proposal_layout.addWidget(self.question)
        self.countdown = QLabel()
        proposal_layout.addWidget(self.countdown)
        self.choices = {}
        for choice, text in (
            ("add_and_select", "加入并切换"),
            ("add_only", "加入但保持当前"),
            ("decline", "拒绝"),
        ):
            button = QPushButton(text)
            decline = choice == "decline"
            button.setIcon(cross_icon(ACTION_GLYPH_SIZE) if decline
                           else plate_check_icon())
            button.setIconSize(QSize(ACTION_GLYPH_SIZE, ACTION_GLYPH_SIZE))
            button.clicked.connect(lambda _checked=False, value=choice: self.resolve(value))
            proposal_layout.addWidget(button)
            self.choices[choice] = button
        layout.addWidget(self.proposal_area)
        self.notice = QLabel()
        self.notice.setTextFormat(Qt.PlainText)
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(200)
        self.update_state(None)

    @property
    def pending_command(self):
        pending = self._pending_command
        return pending is not None and not pending.is_set()

    @pending_command.setter
    def pending_command(self, value):
        self._pending_command = Event() if value else None

    def update_state(self, state):
        controller = self.window.page.session
        scope = (controller.session_id, controller.runtime_id, self.window.page.activation)
        if self.scope != scope:
            self.scope = scope
            self.pending_command = False
            self.proposal = None
            self.latest_result = None
            self.notice.clear()
        self.state = state
        binding = (state or {}).get("binding_state") or {}
        selected = self.targets.currentItem()
        selected_id = selected.data(0, Qt.UserRole) if selected else None
        self.targets.blockSignals(True)
        self.targets.clear()
        default = binding.get("default_target_id")
        holds = binding.get("holds", {})
        for record in binding.get("targets", []):
            key = record["target_id"]
            status = (
                "已关闭或目标已变"
                if key in binding.get("invalidated", {})
                else "默认"
                if key == default
                else "已绑定"
            )
            if holds:
                status += " / 占用中"
            item = QTreeWidgetItem([target_name(record), status])
            item.setData(0, Qt.UserRole, key)
            snapshot = record.get("snapshot", {})
            item.setToolTip(
                0,
                "\n".join(
                    str(value)
                    for value in (key, snapshot.get("window", ""), snapshot.get("ade_session", ""))
                    if value
                ),
            )
            item.setToolTip(1, "\n".join(f"{key}: {value}" for key, value in holds.items()))
            self.targets.addTopLevelItem(item)
            if key == (selected_id or default):
                self.targets.setCurrentItem(item)
        bound_resources = {
            tuple(record.get("snapshot", {}).get("cellview", {}).items())
            for record in binding.get("targets", [])
            if isinstance(record.get("snapshot", {}).get("cellview"), dict)
        }
        for row in binding.get("reservations", []):
            resource = row["context"]
            if tuple(resource["snapshot"]["cellview"].items()) in bound_resources:
                continue
            item = QTreeWidgetItem(
                [target_name(resource), "资源已预留" + (" / 占用中" if holds else "")]
            )
            item.setData(0, Qt.UserRole, row["reservation_id"])
            item.setData(0, Qt.UserRole + 1, "reservation")
            item.setToolTip(
                0, target_name(resource) + "\n" + resource["snapshot"].get("library_path", "")
            )
            self.targets.addTopLevelItem(item)
            if row["reservation_id"] == selected_id:
                self.targets.setCurrentItem(item)
        self.targets.blockSignals(False)
        selected_request = self.operations.currentData()
        self.operations.clear()
        for operation in binding.get("operations", []):
            label = {
                "pending": "待核对",
                "unknown": "结果未知",
                "running": "执行中",
                "not_dispatched": "未派发",
                "completed": "已完成",
                "partial_or_failed": "部分完成或失败",
                "evidence_conflict": "证据不一致",
            }.get(operation["state"], operation["state"])
            request_id = operation["request_id"]
            self.operations.addItem(label + " / " + request_id[:8], request_id)
            self.operations.setItemData(
                self.operations.count() - 1,
                (operation.get("function") or operation["method"]) + "\n" + request_id,
                Qt.ToolTipRole,
            )
        selected_index = self.operations.findData(selected_request)
        if selected_index >= 0:
            self.operations.setCurrentIndex(selected_index)
        self.operations.setVisible(bool(self.operations.count()))
        self.reconcile.setVisible(bool(self.operations.count()))
        policy = binding.get("policy", {})
        self.auto_bind.setChecked(policy.get("auto_bind", False))
        if not self.seconds.hasFocus():
            self.seconds.setValue(policy.get("timeout_seconds", 30))
        proposals = binding.get("proposals", [])
        proposal = proposals[0] if proposals else None
        if proposal and (not self.proposal or self.proposal["event_id"] != proposal["event_id"]):
            self.deadline = time.monotonic() + proposal["remaining_seconds"]
            self.question.setText("加入绑定并切换默认目标？\n" + target_name(proposal["target"]))
            self.question.setToolTip(proposal.get("reason", ""))
            self.notice.clear()
            if self.window.page.reviewing is None:
                self.window.navigation.setCurrentWidget(self)
        self.proposal = proposal
        recent = binding.get("recent", [])
        if recent and recent[-1]["event_id"] != self.latest_result:
            result = recent[-1]
            self.latest_result = result["event_id"]
            error = result.get("error")
            if error:
                self.notice.setText(error["code"] + ": " + error["message"])
                self.notice.setToolTip(str(error.get("data", {})))
            else:
                actor = "超时策略" if result["actor"] == "timeout_policy" else "用户"
                choice = {
                    "add_and_select": "已加入并切换",
                    "add_only": "已加入绑定",
                    "decline": "已拒绝",
                }[result["resolution"]]
                self.notice.setText(actor + "：" + choice + "\n" + target_name(result["target"]))
        self.proposal_area.setVisible(bool(proposal))
        if (state or {}).get("binding_error"):
            self.notice.setText(state["binding_error"])
        elif binding.get("fault"):
            self.notice.setText(binding["fault"])
        self.tick()

    def refresh_controls(self, *_args):
        state = self.state or {}
        binding = state.get("binding_state") or {}
        usable = (
            bool(binding)
            and not self.pending_command
            and self.window.page.reviewing is None
            and not self.window.lifecycle.closing
            and not self.window.page.opening
            and not state.get("closing")
            and not state.get("fault")
            and not state.get("binding_error")
            and not binding.get("fault")
        )
        item = self.targets.currentItem()
        key = item.data(0, Qt.UserRole) if item else None
        different = bool(key and key != binding.get("default_target_id"))
        resource = item and item.data(0, Qt.UserRole + 1) == "reservation"
        self.release.setText("释放资源预留" if resource else "释放绑定")
        self.select.setEnabled(
            usable and different and not resource and key not in binding.get("invalidated", {})
        )
        self.release.setEnabled(
            usable
            and different
            and not state.get("busy")
            and not state.get("pending")
            and not binding.get("holds")
        )
        self.auto_bind.setEnabled(usable)
        self.reconcile.setEnabled(usable and bool(self.operations.count()))
        self.operations.setEnabled(not self.pending_command)
        self.seconds.setEnabled(usable)
        for button in self.choices.values():
            button.setEnabled(usable and bool(self.proposal) and time.monotonic() < self.deadline)

    def tick(self):
        if self.proposal:
            remaining = max(0, math.ceil(self.deadline - time.monotonic()))
            action = (
                "自动加入并切换"
                if self.proposal["timeout_policy"] == "add_and_select"
                else "提议过期"
            )
            self.countdown.setText(f"{remaining} 秒后{action}" if remaining else "等待绑定裁决")
        self.refresh_controls()

    def command(self, name, *args, **kwargs):
        controller, scope = self.window.page.session, self.scope
        self.pending_command = True
        pending = self._pending_command
        self.refresh_controls()

        def current():
            return (self.window.page.session == controller and self.scope == scope
                    and self._pending_command is pending)

        def failed(error):
            if not current():
                return False
            self.notice.setText(str(error))
            self.notice.setToolTip(str(getattr(error, "data", {})))
            self.refresh_controls()
            return False

        def completed(_result):
            if current():
                if name == "reconcile_binding_operation":
                    state = _result["state"]
                    label = {
                        "pending": "原请求尚未结束",
                        "unknown": "证据不足，保留占用",
                        "running": "原操作仍在运行",
                        "not_dispatched": "确认未派发，已解除执行占用",
                        "completed": "确认已完成，已解除执行占用",
                        "partial_or_failed": "确认部分完成或失败，已解除执行占用",
                        "evidence_conflict": "证据不一致，保留占用",
                    }.get(state, state)
                    self.notice.setText(label)
                    self.notice.setToolTip(
                        _result["request_id"] + "\n" + _result["evidence_digest"]
                    )
                else:
                    self.notice.setText("绑定设置已更新")
                self.window.binding.poll()
                self.refresh_controls()

        def settled(_result, _error):
            pending.set()

        self.window.run_command(
            name, *args, controller=controller, success=completed, failure=failed,
            settled=settled, **kwargs
        )

    def target_command(self, name):
        item = self.targets.currentItem()
        if item:
            if item.data(0, Qt.UserRole + 1) == "reservation":
                name = "release_binding_resource"
            self.command(name, item.data(0, Qt.UserRole))

    def reconcile_operation(self, _checked=False):
        request_id = self.operations.currentData()
        binding = (self.state or {}).get("binding_state") or {}
        if request_id and binding.get("binding_id"):
            self.command("reconcile_binding_operation", binding["binding_id"], request_id)

    def resolve(self, choice):
        if self.proposal:
            self.command("resolve_binding", self.proposal["event_id"], choice)

    def save_policy(self, _checked=False):
        policy = ((self.state or {}).get("binding_state") or {}).get("policy", {})
        updated = dict(auto_bind=self.auto_bind.isChecked(), timeout_seconds=self.seconds.value())
        if policy and policy != updated and not self.pending_command:
            self.command("configure_bindings", **updated)
