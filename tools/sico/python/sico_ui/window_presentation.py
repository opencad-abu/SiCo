"""Render the live conversation and its task controls from owned state."""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QLabel, QSizePolicy

from .presentation import target_label, token_amount
from .session_activation import PREVIEW_HINT
from .tasks import router_tooltip, session_status


class UsageLabel(QLabel):
    """Single-line usage readout that elides instead of wrapping."""

    MINIMUM_WIDTH = 80

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWordWrap(False)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.setMinimumWidth(0)
        self._full_text = ""

    def setText(self, text):
        self._full_text = text
        super().setText(text)
        self._apply_elide()

    def fullText(self):
        return self._full_text

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        hint.setWidth(min(hint.width(), self.MINIMUM_WIDTH))
        return hint

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_elide()

    def _apply_elide(self):
        width = self.width()
        if width <= 0 or not self._full_text:
            return
        super().setText(self.fontMetrics().elidedText(self._full_text, Qt.ElideMiddle, width))


class WindowPresentation:
    def __init__(self, widgets, *, page, presentation, receipts, centers, navigation,
                 bindings, steering, activity_timer, refresh_receipt, refresh_tasks,
                 run_command, poll):
        self.ui = widgets
        self.page = page
        self.presentation = presentation
        self.receipts = receipts
        self.centers = centers
        self.navigation = navigation
        self.binding_panel = bindings
        self.steering_input = steering
        self.activity_timer = activity_timer
        self.refresh_router_receipt = refresh_receipt
        self.refresh_task_panel = refresh_tasks
        self.run_command = run_command
        self.poll = poll

    def receive(self, event):
        if not self.presentation.receive(event):
            return
        if self.page.reviewing is None:
            self.centers.receive(event)

    def update_session(self, state, *, contexts=None):
        self.binding_panel.update_state(state)
        if self.presentation.update(state, contexts):
            self.ui.status_bar.clearMessage()
        self.refresh_router_receipt()
        self.refresh_busy_cursor()
        self.sync_activity_animation()
        self._update_token_usage(state.get("task", {}))
        if self.page.reviewing is not None:
            # History browsing is read-only: the live session keeps running and
            # its controls stay out of the preview until "继续" activates it.
            self.refresh_status()
            return
        label = target_label(self.presentation.submission_context.snapshot)
        if self.presentation.source_detached:
            label = label.replace("当前目标：", "原来源：") + "（窗口已关闭或切换，工程绑定有效）"
        self.ui.target.setText(label)
        self.refresh_status()
        self.steering_input.refresh()
        self.ui.stop.setEnabled(state["busy"] and not state["cancelling"] and not state["closing"])
        needs_reconcile = state["task"].get("status") == "needs_reconcile"
        self.ui.abandon.setVisible(False)  # Decisions belong to the addressed chat card.
        self.ui.abandon.setEnabled(not state["busy"] and not state["fault"])
        self.ui.resume.setText(f"执行待办任务（{state['pending']}）")
        self.ui.resume.setToolTip(self.queue_hold_reason() or "按接收顺序执行待办请求")
        self.ui.resume_action.setToolTip(self.ui.resume.toolTip())
        self.ui.resume.setVisible(state["paused"] and state["pending"] > 0)
        self.ui.resume.setEnabled(not state["busy"] and not needs_reconcile and not state["fault"])
        self.ui.resume_action.setVisible(state["paused"] and state["pending"] > 0)
        self.ui.resume_action.setEnabled(
            state["paused"] and state["pending"] > 0 and self.ui.resume.isEnabled()
        )
        if not getattr(self.page.api, "can_control", lambda _token: True)(self.page.session):
            for control in (self.ui.stop, self.ui.abandon, self.ui.resume,
                            self.ui.resume_action):
                control.setEnabled(False)
        self.refresh_task_panel()
        self.navigation.setTabText(1, "任务队列")
        self.navigation.setTabToolTip(1, f"待执行：{state['pending']}")

    def _update_token_usage(self, task):
        """Render model usage beside the current target without exposing internals."""
        task = task if isinstance(task, dict) else {}
        input_tokens = task.get("input_tokens", 0)
        output_tokens = task.get("output_tokens", 0)
        if type(input_tokens) is not int or input_tokens < 0:
            input_tokens = 0
        if type(output_tokens) is not int or output_tokens < 0:
            output_tokens = 0
        total = input_tokens + output_tokens
        summary = (
            "词元：输入 " + token_amount(input_tokens)
            + " · 输出 " + token_amount(output_tokens)
            + " · 合计 " + token_amount(total)
        )
        self.ui.token_usage.setText(summary)
        # The readout stays on one line; hovering always shows the full figures.
        self.ui.token_usage.setToolTip("当前任务的模型词元消耗；输入和输出分别统计\n" + summary)
        self._fit_token_usage()

    def _fit_token_usage(self):
        """Keep the usage readout on one line; elide only when the row is narrow."""
        label = self.ui.token_usage
        text = label.fullText()
        if not text:
            return
        full = label.fontMetrics().horizontalAdvance(text) + 4
        parent = label.parentWidget()
        row = parent.width() if parent is not None else full
        limit = max(label.MINIMUM_WIDTH, row * 2 // 3)
        label.setFixedWidth(max(label.MINIMUM_WIDTH, min(full, limit)))

    def refresh_status(self):
        self.refresh_router_receipt()
        state = self.presentation.state or {}
        if self.page.opening:
            self.ui.status.setText("正在打开会话…")
        elif self.page.reviewing is not None:
            self.ui.status.setText(PREVIEW_HINT)
        elif self.page.api.is_closing(self.page.session):
            self.ui.status.setText("正在结束会话…" if state.get("busy") else "会话已结束")
        elif self.presentation.state is not None:
            self.ui.status.setText(session_status(self.presentation.state))
        if state.get("fault") and self.page.reviewing is None and not self.page.opening:
            self.ui.session_notices.set("fault", "会话故障", state["fault"], level="error")
        else:
            self.ui.session_notices.clear("fault")
        detail = ""
        if self.page.reviewing is None and not self.page.opening:
            detail = "\n".join(text for text in (
                self.queue_hold_reason(), state.get("task", {}).get("diagnostic", ""),
                router_tooltip(state.get("router")),
            ) if text)
        self.ui.status.setToolTip(detail)

    def refresh_busy_cursor(self):
        """Busy pointer only while a page is loading.

        模型思考与工具执行不改光标：气泡里的工具结果始终看起来可点。只有
        打开会话/读取或继续历史记录这类把页面内容读进来的过程才显示 busy。
        """
        self.ui.display.set_busy(bool(self.page.opening))

    def cancel_task(self, _checked=False):
        self.run_command("cancel")
        self.poll()

    def resume_queue(self, _checked=False):
        self.run_command("resume")
        self.poll()

    def abandon_interrupted(self, _checked=False):
        self.run_command("acknowledge_interrupted", success=self._resume_after_reconcile)
        self.poll()

    def _resume_after_reconcile(self, *_result):
        """After the interrupted task is abandoned, hand the queue over to resume."""
        state = self.presentation.state or {}
        if state.get("pending", 0) > 0 and not state.get("fault"):
            self.resume_queue()

    def queue_hold_reason(self):
        """Why the pending queue cannot run right now; empty when it can."""
        state = self.presentation.state or {}
        if not (state.get("paused") and state.get("pending", 0) > 0):
            return ""
        task = state.get("task", {})
        if task.get("status") == "needs_reconcile":
            detail = task.get("diagnostic") or "上一轮操作的结果没有确认"
            return "队列已暂停：请先结束中断任务（" + detail + "）"
        if state.get("fault"):
            return "队列已暂停：会话处于故障状态，请结束会话后重新发起"
        if state.get("busy"):
            return "队列已暂停：当前任务仍在执行，结束后会自动继续"
        return "队列已暂停"

    def _tick_activity(self):
        """Animate the in-flight bubble so a running task never looks frozen."""
        if not self.presentation.transcript.advance_activity():
            self.activity_timer.stop()
        self.flush()

    def _follow_activity(self):
        """Animate exactly while the live transcript holds an in-flight mark.

        动效由气泡/计数本身驱动，不再依赖会话快照的 busy：快照落后或另有
        后台任务时，正在执行的计数仍然会动；标记结束后只留下展开/收拢三角形。
        """
        busy = self.ui.parent.isVisible() and bool(self.presentation.transcript.live_messages())
        if busy and not self.activity_timer.isActive():
            self.activity_timer.start()
        elif not busy and self.activity_timer.isActive():
            self.activity_timer.stop()
            return self.presentation.transcript.settle_activity()
        return False

    def sync_activity_animation(self):
        """Re-evaluate the running-mark animation after a state or view change."""
        if self._follow_activity():
            self.flush()

    def flush(self):
        self._follow_activity()
        if self.page.reviewing is None:
            self.ui.parent.chat_scroll.render()
            self.centers.flush()

    def replay_caught_up(self):
        """The event cursor reached the journal head; centers may load now."""
        self.centers.release_snapshot()
