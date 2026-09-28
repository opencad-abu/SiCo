"""Present project-service connection state and keep this window attached to it."""

import time

from PyQt5.QtCore import QObject, Qt, QTimer
from PyQt5.QtWidgets import QLabel

from .receipts import DataReceipt

RETRY_SECONDS = 3.0
RETRY_LIMIT = 10
GONE = "项目服务未响应"


def attach_service_connection(window):
    """Give a project-service client window its connection and recovery presenters.

    Only a frontend that publishes ``connection_state`` (the service client)
    carries service state; the in-process desktop keeps none.
    """

    if not hasattr(window.api, "connection_state"):
        return None
    connection = ServiceConnection(window)
    window.service_connection = connection
    if hasattr(window.api, "platforms"):
        from .platform_menu import PlatformMenu

        window.platform_menu = PlatformMenu(window,
            lambda frontend: window.page.activate_session(frontend.session.session_id))
    return connection


def service_recovery(window):
    """The conversation continuation presenter of a project-service window."""

    connection = getattr(window, "service_connection", None)
    return None if connection is None else connection.recovery


def service_ending(window):
    """The session-ending presenter of a project-service window, else ``None``."""

    connection = getattr(window, "service_connection", None)
    return None if connection is None else connection.ending


class ServiceConnection(QObject):
    LABELS = {"connecting": "正在连接项目服务…", "connected": "项目服务已连接",
              "disconnected": "项目服务连接已断开，正在自动重连…",
              "reconnecting": "正在重连项目服务…", "detached": "已分离项目服务"}

    def __init__(self, window):
        from .service_control import ServiceControl
        from .session_ending import SessionEnding
        from .session_recovery import SessionRecovery

        super().__init__(window)
        self.window = window
        # 服务状态统一挂在状态栏中间；先于控制权限标签登记，读起来是
        # “项目服务已连接 · 其他窗口正在使用此会话”。
        self.label = QLabel()
        self.label.setTextFormat(Qt.PlainText)
        window.add_service_status(self.label)
        self.control = ServiceControl(window)
        # “继续”直接打开原会话，恢复细节留在服务内。
        self.recovery = SessionRecovery(window)
        self.ending = SessionEnding(window)
        from .force_service_stop import FORCE_STOP

        self.stop_action = window.service_action("停止", window.close_choice.stop_everything)
        self.stop_action.setToolTip("结束本窗口打开的会话，收尾完成后停止项目服务并关闭窗口")
        self.force_action = window.service_action(FORCE_STOP, window.close_choice.force.confirm)
        # 断线自动重连；只有反复失败才留一个手动入口，不让用户先点菜单。
        self.retry_action = window.session_action("重试连接…", self.reconnect)
        self.retry_action.setVisible(False)
        self._failures = 0
        self._due = None
        self._attempt = None
        self._automatic = False
        self.receipt = DataReceipt(self)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(250)
        self.refresh()

    def attached(self):
        """True while the service lanes and this window's own replay are both healthy."""
        return (self.window.api.connection_state == "connected"
                and not self.window.page.opening and self.window.binding.cursor is not None
                and not self.window.binding.failed)

    def refresh(self):
        window = self.window
        if window.lifecycle.closing:
            self.receipt.clear()
            self.timer.stop()
            return
        if getattr(self.window.close_choice, "busy", False):
            # 关闭流程正在结束会话：不重连、不改连接提示，避免和收尾抢状态。
            self.label.setText("正在强制停止 Agent Service…" if window.close_choice.force.pending
                               else "正在结束会话并停止项目服务…")
            return
        if window.api.is_closing(window.page.session):
            self._due = self._attempt = None
            window.session_notices.clear("connection")
            self.retry_action.setVisible(False)
            self.label.setText("会话已结束或正在收尾")
            return
        if self.attached():
            window.session_notices.clear("connection")
            self._failures, self._due = 0, None
            self._attempt = None
            self.retry_action.setVisible(False)
            self.label.setText(self.LABELS["connected"])
            return
        now = time.monotonic()
        if self._attempt is not None and self._attempt != window.page.activation:
            self._attempt = None
        if window.page.opening:
            self.label.setText(self.LABELS["reconnecting"])
            return
        if self._failures >= RETRY_LIMIT:
            self.label.setText(GONE)
            self.retry_action.setVisible(True)
            return
        if self._due is None:
            self._due = now + RETRY_SECONDS
        elif now >= self._due:
            self.reconnect(automatic=True)
        self.retry_action.setVisible(self._failures > 2)
        if self._failures >= RETRY_LIMIT:
            self.label.setText(GONE)
        elif window.api.connection_state == "connected":
            self.label.setText("会话显示已断开，正在自动重连…")
        else:
            self.label.setText(self.LABELS.get(window.api.connection_state,
                                               self.LABELS["disconnected"]))

    def stop_service(self, settled, failed):
        """Ask the service to stop; the caller owns what happens next."""
        try:
            self.receipt.watch(self.window.api.stop_service(), settled, failed)
        except (ValueError, RuntimeError) as exc:
            failed(exc)

    def replay_failed(self, message):
        """Route automatic replay failures in place; explicit retries use InfoApp."""
        if self._attempt != self.window.page.activation:
            return False
        self._attempt = None
        self._due = time.monotonic() + RETRY_SECONDS
        self._failure_notice(message)
        return True

    def _failure_notice(self, message):
        message = str(message) + "\n会话记录已保留；可从会话菜单重试连接或重新打开 SiCo。"
        self.window.session_notices.set("connection", "会话连接未恢复", message)
        if not self._automatic:
            self.window.info_app.warning("会话连接未恢复", message)

    def reconnect(self, _checked=False, *, automatic=False):
        """Rebuild this window's subscription on the captured service; never resumes tasks."""
        window = self.window
        if (window.lifecycle.closing or window.close_choice.busy or window.page.opening
                or window.api.is_closing(window.page.session)):
            return
        if not automatic:
            self._failures = 0
        self._automatic = automatic
        self._failures += 1
        self._due = time.monotonic() + RETRY_SECONDS
        self.label.setText(self.LABELS["reconnecting"])
        try:
            if window.binding.delivery is not None:
                window.api.reconnect_session(window.page.session, window.binding.delivery.stream)
            self._attempt = window.page.activation + 1
            window.page.activate()
        except (ValueError, OSError, RuntimeError, EOFError) as exc:
            self._attempt = None
            self.label.setText("会话连接未恢复")
            self._failure_notice(exc)
