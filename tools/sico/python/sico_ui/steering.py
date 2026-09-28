"""Default current-turn input and an explicit queue action, using UI snapshots."""

import uuid
from copy import deepcopy

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import QHBoxLayout, QMenu, QToolButton

DEFER_ACTION = "当前任务完成后执行"
DEFER_HINT = f"从发送按钮的下拉菜单选择“{DEFER_ACTION}”"
WAIT_HINT = f"当前会话暂不能立即启动新任务，草稿保留；可{DEFER_HINT}"


class SteeringInput:
    def __init__(self, widgets, buttons, *, page, presentation, lifecycle, receipts,
                 submissions, drafts, queue_input, stream_failed, open_audits, run_command,
                 update, poll):
        self.ui = widgets
        self.page, self.presentation, self.lifecycle = page, presentation, lifecycle
        self.receipts, self.submissions, self.drafts = receipts, submissions, drafts
        self.stream_failed, self.open_audits = stream_failed, open_audits
        self.run_command, self.update, self.poll = run_command, update, poll
        self.build_menu(buttons, queue_input)
        # Read only the UI snapshot; session activation can prepare it asynchronously.
        self.timer = QTimer(widgets.parent)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()

    def build_menu(self, buttons, queue_input):
        menu = QMenu(self.ui.parent)
        self.queue_action = menu.addAction(DEFER_ACTION, queue_input)
        self.queue_action.setToolTip("将输入保存为当前会话的下一阶段任务，按顺序执行")
        self.queue_action.setEnabled(False)
        self.menu_button = QToolButton()
        self.menu_button.setObjectName("sendOptions")
        self.menu_button.setAccessibleName("发送选项")
        self.menu_button.setToolTip("发送选项：安排当前会话的下一阶段任务")
        self.menu_button.setProperty("bottomAction", True)
        self.menu_button.setArrowType(Qt.DownArrow)
        self.menu_button.setFixedWidth(26)
        self.menu_button.setMenu(menu)
        self.menu_button.setPopupMode(QToolButton.InstantPopup)
        self.menu_button.setEnabled(False)
        menu.aboutToShow.connect(self.refresh)
        # Keep the arrow usable when steering is blocked but a later task is allowed.
        index = buttons.indexOf(self.ui.send)
        buttons.removeWidget(self.ui.send)
        split = QHBoxLayout()
        split.setSpacing(0)
        split.addWidget(self.ui.send)
        split.addWidget(self.menu_button)
        buttons.insertLayout(index, split)

    def available(self):
        state = self.presentation.state or {}
        key = (self.page.session.session_id, self.page.session.runtime_id)
        return (bool(state) and self.page.reviewing is None and not self.page.opening
                and getattr(self.page.api, "can_control", lambda _token: True)(self.page.session)
                and not self.lifecycle.closing and self.ui.input.isEnabled()
                and not self.ui.input.isReadOnly()
                and not state.get("closing") and not state.get("fault")
                and not self.stream_failed()
                and key not in self.submissions)

    def scope(self):
        state = self.presentation.state or {}
        scope = state.get("steering")
        if (not scope or not self.available() or state.get("cancelling")
                or self.open_audits()
                or scope["context"] != self.presentation.submission_context.record()):
            return None
        return scope

    def routes_to_current(self):
        """Keep busy input on its intended task even while steering is unavailable."""
        state = self.presentation.state or {}
        return (state.get("steering_supported", False) and state.get("busy", False)
                and state.get("context") == self.presentation.submission_context.record())

    def new_task_waits(self):
        state = self.presentation.state or {}
        terminal = (state.get("task") or {}).get("status") in {"completed", "failed", "cancelled"}
        return bool(state.get("busy") or state.get("pending")
                    or state.get("paused") and not terminal)

    def refresh(self):
        state = self.presentation.state or {}
        available = self.available()
        queued = state.get("pending", 0) < 16
        self.queue_action.setEnabled(available and queued)
        self.menu_button.setEnabled(self.queue_action.isEnabled())
        waiting = bool(state.get("busy") and self.open_audits())
        if waiting:
            label, hint, enabled = "答复问题", "打开当前待答问题", available
        elif self.routes_to_current():
            label = "补充当前任务"
            hint = f"Enter 补充当前任务；Shift+Enter 换行；后续任务请{DEFER_HINT}"
            enabled = available and self.scope() is not None
            if not enabled:
                hint = f"当前回合暂不能接收补充，草稿保留；后续任务可{DEFER_HINT}"
        elif self.new_task_waits():
            label, hint, enabled = "发送", WAIT_HINT, False
        else:
            label = "发送"
            hint = "发送任务（Enter 发送，Shift+Enter 换行）"
            enabled = available and queued
        self.ui.send.setText(label)
        self.ui.send.setToolTip(hint)
        self.ui.send.setEnabled(enabled)
        self.menu_button.setFixedHeight(self.ui.send.sizeHint().height())
        if self.lifecycle.closing:
            self.timer.stop()

    def submit(self, _checked=False):
        scope = deepcopy(self.scope())
        if scope is None:
            self.ui.notices.notice("补充暂未发送", f"当前回合暂不能接收补充，草稿保留；后续任务可{DEFER_HINT}")
            return False
        if self.ui.input.text_attachment() is not None or self.ui.input.turn_inputs:
            self.ui.notices.notice("补充暂未发送", f"当前补充入口接受文本；附件草稿保留，可{DEFER_HINT}")
            return False
        if self.ui.input.turn_options is not None:
            self.ui.notices.notice("回合设置草稿保留", f"请{DEFER_HINT}")
            return False
        text = self.ui.input.toPlainText().strip()
        if not text:
            return False
        controller, activation = self.page.session, self.page.activation
        key, draft = (controller.session_id, controller.runtime_id), self.ui.input.draft()
        revision = self.ui.input.draft_revision
        if not self.submissions.begin(key):
            return False
        submissions, drafts = self.submissions, self.drafts
        self.refresh()
        self.ui.send.setEnabled(False)

        def current():
            return (self.page.session == controller and self.page.activation == activation
                    and self.page.reviewing is None and not self.lifecycle.closing)

        def settled(result, error):
            submissions.finish(key)
            if error is None and result.get("status") == "accepted":
                drafts.accept(controller, revision)

        def settle():
            if current() and self.presentation.state:
                self.update(self.presentation.state)
                self.poll()
            self.refresh()

        def received(result):
            settle()
            if not current():
                return
            if (result["status"] == "accepted" and self.ui.input.draft_revision == revision
                    and self.ui.input.draft()[:2] == draft[:2]):
                self.ui.input.clear()
            if result["status"] != "accepted":
                self.ui.notices.warning("补充未接收", result["message"])
            else:
                self.ui.status.setText("补充已接收")
                self.receipts.clear_error()

        def failed(exc):
            settle()
            if not current():
                return False
            self.ui.notices.error("补充未发送", "草稿保留：" + str(exc))
            return False

        self.run_command("steer", text, scope=scope, request_id=uuid.uuid4().hex,
                           controller=controller, success=received, failure=failed, settled=settled)
        return True
