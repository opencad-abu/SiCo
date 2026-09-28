"""Present and observe the same session-end operation for row actions and window closing."""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QLabel

from .session_end_group import SessionEndGroup
from .si_prompt import CANCEL, SiConfirm


class SessionEnding(SessionEndGroup):
    def __init__(self, window):
        super().__init__(window)
        self.dialog = None
        self.label = QLabel()
        self.label.setTextFormat(Qt.PlainText)
        window.add_service_status(self.label)
        self.query = window.session_action("核对会话结束结果", self.observe)
        self.refresh()

    def refresh(self):
        window = self.window
        if not super().refresh():
            if self.dialog is not None:
                self.dialog.reject()
            return
        token = window.page.session
        entry = self.operations.get(token)
        unresolved = entry is not None and entry.state in {"unknown", "closing", "needs_reconcile"}
        self.query.setVisible(unresolved)
        self.query.setEnabled(bool(unresolved and not entry.pending and not window.page.opening))
        text = self.TEXT[entry.state] if entry else (
            "会话已结束或正在收尾" if window.api.is_closing(token) else "")
        if entry and entry.pending and entry.state == "unknown":
            text = "正在核对会话结束结果…"
        self.label.setText(text)
        self.label.setToolTip((entry.error or self.DETAILS.get(entry.state, "")) if entry else "")

    def confirm(self, session_id=None):
        window = self.window
        if self.dialog is not None or window.lifecycle.closing:
            return
        token = (window.page.session if session_id in (None, window.page.session.session_id)
                 else window.api.session(session_id))
        if token is None or token.runtime_id is None:
            window.info_app.notice("无需结束会话", "此记录没有活动会话，已归档记录无需再次归档。")
            return
        try:
            title = window.api.display_name(token) or token.session_id
        except (OSError, RuntimeError, ValueError):
            title = token.session_id
        dialog = SiConfirm("结束并归档",
                           f"结束「{title}」并归档到历史栏？\n"
                           "它不再接收输入，正在执行的任务会停止，未开始的队列保留为未执行；\n"
                           "其他会话与项目服务不受影响，已发出的外部操作可能仍需核对。",
                           window)
        dialog.add_choice("accept", "结束并归档", default=True)
        dialog.add_choice("cancel", CANCEL, escape=True)
        self.dialog = dialog

        def finished(key):
            self.dialog = None
            dialog.deleteLater()
            if (key == "accept" and not window.lifecycle.closing
                    and window.api.session(token.session_id) == token):
                self.request(token)
        dialog.chosen.connect(finished)
        dialog.open()

    def request(self, token):
        self.end(token, lambda _result: None,
                 lambda error: self.window.info_app.warning("结束会话未完成",
                     "会话：" + token.session_id + "\n" + str(error)))

    def _changed(self):
        if not self.window.lifecycle.closing:
            self.refresh()
            if self.window.presentation.state is not None:
                self.window.update_session(self.window.presentation.state)

    def observe(self):
        token = self.window.page.session
        if token in self.operations:
            self.end(token,
                lambda _result: self.window.info_app.info("会话结束结果",
                    "会话：" + token.session_id + "\n" + self.DETAILS["ended"]),
                lambda error: self.window.info_app.warning("会话结束结果待核对",
                    "会话：" + token.session_id + "\n" + str(error)))
