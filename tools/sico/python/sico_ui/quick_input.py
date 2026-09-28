"""Forward captured Ask inputs to the worker's design-based session routing."""

from __future__ import annotations

from PyQt5.QtCore import QObject
from sico.core.contracts import identifier

from .glyphs import ACTION_GLYPH_SIZE, plus_icon


class QuickInput(QObject):
    def __init__(self, window, notice):
        super().__init__(window)
        self.window, self.notice = window, notice
        controller = window.page.session

        def failed(exc):
            window.binding.poll()
            window.info_app.error("会话目标未注册", str(exc))
            return False

        window.run_command("attach_targets", controller=controller, failure=failed)
        self.new = window.session_action("新会话", self.new_session, "Ctrl+N")
        self.new.setToolTip("在当前窗口新建会话页面；Ask 根据来源设计的绑定选择会话")
        self.new.setIcon(plus_icon(ACTION_GLYPH_SIZE))

    def new_session(self):
        if not self.window.api.is_closing(self.window.page.session):
            self.window.new_session()

    @property
    def controller(self):
        """Resolve the active page's controller instead of pinning the first one."""

        return self.window.page.session

    def accept(self, message):
        controller = self.controller
        scope = self.window.actions.capture(controller)
        notification = {}
        notify = self.notice

        def settled(_result, error):
            try:
                request_id = identifier(message.get("id"))
            except (ValueError, AttributeError):
                return
            kind = "accepted" if error is None else "rejected"
            notification["sent"] = notify(kind, id=request_id)

        def notice_failure(accepted):
            if notification.get("sent") is False:
                self.window.update_host_notices("failed")
                text = (
                    "快速输入已接收，但宿主未收到确认；请在本窗口查看任务，勿重复提交。"
                    if accepted else "快速输入未接收，且宿主回执发送失败。"
                )
                self.window.info_app.warning("快速输入回执未送达", text)
                self.window.host_notice_status.setText(text)

        def failed(exc):
            self.window.info_app.error("快速输入未接收", str(exc))
            notice_failure(False)
            return False

        def accepted(result):
            # A durable receipt must reach the host even if opening its page
            # fails. Late receipts never override a user's subsequent view.
            if self.window.page.reviewing is None:
                if result.session == controller:
                    self.window.binding.poll()
                else:
                    self.window.activate_session(result.session.session_id)
            self.window.restore()
            notice_failure(True)

        try:
            future = self.window.api.accept_quick(controller, message)
        except (ValueError, RuntimeError) as exc:
            self.window.commands.rejected(exc, success=accepted, failure=failed,
                                          settled=settled, scope=scope)
            return
        self.window.commands.watch(future, success=accepted, failure=failed,
                                   settled=settled, scope=scope)

    def close(self):
        self.window.api.close_desktop()
