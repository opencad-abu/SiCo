"""Confirm and dispatch a history-only rewind from one user message."""

from __future__ import annotations

from uuid import uuid4

from PyQt5.QtCore import QObject
from PyQt5.QtGui import QTextCursor

from .receipt_scope import ReceiptScope
from .si_prompt import confirm


class RewindActions(QObject):
    """The click-to-operation bridge for a rewindable user bubble."""

    def __init__(self, widgets, *, api, page, presentation, lifecycle, run_command):
        super().__init__(widgets.parent)
        self.ui = widgets
        self.api = api
        self.page = page
        self.presentation = presentation
        self.lifecycle = lifecycle
        self.run_command = run_command
        self.attempt = None
        page.invalidated.connect(self.invalidate)

    def invalidate(self):
        self.attempt = None

    def open(self, message_id):
        try:
            message_id = int(message_id)
        except (TypeError, ValueError):
            return
        if self.attempt is not None:
            self.ui.notices.notice("回退正在处理", "请等待当前回退操作完成")
            return
        reason = self._blocked_reason()
        if reason:
            self.ui.notices.warning("暂时不能回退", reason)
            return
        transcript = self.ui.parent.chat_scroll.shown()
        message = next(
            (
                row
                for row in (transcript.messages if transcript else ())
                if row.get("message_id") == message_id
            ),
            None,
        )
        if message is None or message.get("role") != "user":
            self.ui.notices.warning(
                "无法回退", "这条消息已不在当前会话视图中，请重新打开会话"
            )
            return
        turn_id, thread_id = message.get("turn_id"), message.get("thread_id")
        if (
            not isinstance(turn_id, str)
            or not turn_id
            or not isinstance(thread_id, str)
        ):
            self.ui.notices.warning("无法回退", "这条消息没有可回退的会话回合")
            return
        if not confirm(
            self.ui.parent,
            "回退会话",
            "将删除这条用户消息及其后的全部会话回合。\n"
            "工作区文件和 OA 修改不会回退，审计记录仍会保留。\n"
            "输入框为空时，会把原消息放回输入框。\n"
            "是否继续？",
            choice="回退",
            danger=True,
        ):
            return
        scope = ReceiptScope.capture(self.page, self.presentation.displayed_context)
        self.attempt = {
            "turn_id": turn_id,
            "thread_id": thread_id,
            "text": message.get("text", ""),
            "scope": scope,
        }
        self.ui.status_bar.showMessage("正在检查会话状态…", 5000)
        self.run_command(
            "thread_status",
            success=lambda snapshot: self._status_ready(snapshot),
            failure=self._failed,
            receipt_scope=scope,
        )

    def reverted(self, event):
        """Restore the prompt only after the verified revert event arrives."""
        attempt = self.attempt
        payload = event.get("payload") or {}
        if attempt is None or (
            payload.get("thread_id") != attempt["thread_id"]
            or payload.get("before_turn_id") != attempt["turn_id"]
        ):
            return
        self.attempt = None
        if not self._current(attempt["scope"]):
            return
        text = attempt["text"]
        if not text:
            return
        if self.ui.input.toPlainText().strip():
            self.ui.notices.notice(
                "回退完成", "输入框已有草稿，原消息未回填；审计记录中仍保留原文"
            )
            return
        self.ui.input.setPlainText(text)
        self.ui.input.moveCursor(QTextCursor.End)
        self.ui.input.setFocus()

    def _status_ready(self, snapshot):
        attempt = self.attempt
        if attempt is None or not self._current(attempt["scope"]):
            return
        if (
            not isinstance(snapshot, dict)
            or snapshot.get("history_mode") != "paginated"
        ):
            self._reject("旧格式会话不支持历史回退；历史保留，可继续会话")
            return
        operation_scope = snapshot.get("scope")
        if (
            not isinstance(operation_scope, dict)
            or operation_scope.get("thread_id") != attempt["thread_id"]
            or snapshot.get("thread_id") != attempt["thread_id"]
        ):
            self._reject("这条消息属于已切换的会话线程，不能回退")
            return
        if snapshot.get("busy"):
            self._reject("请先结束当前任务")
            return
        operation = snapshot.get("operation")
        if isinstance(operation, dict) and operation.get("status") == "executing":
            self._reject("请等待当前会话操作完成")
            return
        request_id = uuid4().hex
        self.run_command(
            "thread_operation",
            {"kind": "rollback_to", "before_turn_id": attempt["turn_id"]},
            scope=operation_scope,
            request_id=request_id,
            success=lambda _request: self._accepted(attempt),
            failure=self._failed,
            receipt_scope=attempt["scope"],
        )

    def _accepted(self, attempt):
        if self.attempt is attempt:
            self.ui.status_bar.showMessage("回退请求已接收", 5000)

    def _failed(self, exc):
        self.attempt = None
        self.ui.notices.error("回退未完成", str(exc))
        return False

    def _reject(self, message):
        self.attempt = None
        self.ui.notices.warning("无法回退", message)

    def _current(self, scope):
        return (
            self.page.reviewing is None
            and not self.api.is_closing(self.page.session)
            and scope.current(
                self.page,
                self.presentation.displayed_context,
                self.api,
                self.lifecycle.closing,
            )
        )

    def _blocked_reason(self):
        state = self.presentation.state or {}
        if self.page.reviewing is not None:
            return "只读历史会话不能回退"
        if self.page.opening:
            return "会话仍在加载，请稍后"
        if self.lifecycle.closing:
            return "窗口正在关闭"
        if self.api.is_closing(self.page.session):
            return "会话正在结束"
        if state.get("busy") or state.get("cancelling"):
            return "请先结束当前任务"
        if state.get("pending"):
            return "请先处理待办输入"
        if state.get("fault"):
            return "会话处于故障状态，请先结束或重新打开会话"
        if (state.get("task") or {}).get("status") == "needs_reconcile":
            return "请先处理待核对的中断任务"
        return ""
