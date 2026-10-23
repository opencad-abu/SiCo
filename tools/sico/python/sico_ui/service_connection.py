"""Present project-service connection state and keep this window attached to it."""

from PyQt5.QtCore import QObject, Qt, QTimer
from PyQt5.QtWidgets import QLabel

from .receipts import DataReceipt
from .service_retry import ServiceRetry

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
        self._retry = ServiceRetry(limit=RETRY_LIMIT, delay=RETRY_SECONDS, immediate=False)
        self._attempt = None
        self._automatic = False
        self._startup = None
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
            self._cancel_startup()
            self.receipt.clear()
            self.timer.stop()
            return
        if getattr(self.window.close_choice, "busy", False):
            # 关闭流程正在结束会话：不重连、不改连接提示，避免和收尾抢状态。
            self.label.setText("正在强制停止智能体服务…" if window.close_choice.force.pending
                               else "正在结束会话并停止项目服务…")
            return
        if window.api.is_closing(window.page.session):
            self._cancel_startup()
            self._attempt = None
            window.session_notices.clear("connection")
            self.retry_action.setVisible(False)
            # 会话结束状态由主状态行与结束面板表达；这里只报连接本身，
            # 不再和状态行重复一条“已结束”文案。
            self.label.setText(self.LABELS["connected"])
            return
        if self.attached():
            self._cancel_startup()
            window.session_notices.clear("connection")
            self._retry.healthy()
            self._attempt = None
            self.retry_action.setVisible(self.control.exhausted)
            self.label.setText(self.LABELS["connected"])
            return
        self._retry.unhealthy()
        if self._poll_startup():
            return
        if self._attempt is not None and self._attempt != window.page.activation:
            self._attempt = None
        if window.page.opening:
            self.label.setText(self.LABELS["reconnecting"])
            return
        if self._retry.exhausted:
            self.label.setText(GONE)
            self.retry_action.setVisible(True)
            return
        self.reconnect(automatic=True)
        self.retry_action.setVisible(self._retry.attempts > 2 or self.control.exhausted)
        if self._retry.exhausted:
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

    def _cancel_startup(self):
        if self._startup is not None:
            self._startup.close()
            self._startup = None

    def _poll_startup(self):
        startup = self._startup
        if startup is None:
            return False
        if not startup.ready.done():
            self.label.setText("正在启动项目服务…")
            return True
        self._startup = None
        try:
            descriptor = startup.take_descriptor()
        except Exception as exc:
            startup.close()
            self._retry.unhealthy()
            self._failure_notice("项目服务启动失败：" + str(exc))
            return True
        startup.close()
        if not self.window.api._rebind_service(descriptor):
            self._activate_current()
        else:
            self.label.setText("项目服务已重新连接，正在恢复会话…")
            self.window.page.continue_session(self.window.page.session.session_id)
        return True

    def _activate_current(self):
        """Reconnect the page to a service that is still the same instance."""
        window = self.window
        try:
            if window.binding.delivery is not None:
                window.api.reconnect_session(window.page.session, window.binding.delivery.stream)
            self._attempt = window.page.activation + 1
            window.page.activate()
        except (ValueError, OSError, RuntimeError, EOFError) as exc:
            self._attempt = None
            self.label.setText("会话连接未恢复")
            self._failure_notice(exc)

    def replay_failed(self, message):
        """Route automatic replay failures in place; explicit retries use InfoApp."""
        if self._attempt != self.window.page.activation:
            return False
        self._attempt = None
        self._retry.unhealthy()
        self._failure_notice(message)
        return True

    def recover_stream(self):
        """Replace a broken event subscription from its captured window start.

        服务窗口的任务不因事件流中断而暂停：窗口会被原样重放，重复 sequence
        在客户端丢弃。恢复按有上限的退避重试，耗尽后保留手动重试入口。
        """

        window = self.window
        if (self._retry.exhausted
                or window.lifecycle.closing or window.close_choice.busy
                or window.api.is_closing(window.page.session)
                or window.binding.delivery is None):
            return False
        self._retry.unhealthy()
        window.session_notices.set("connection", "会话事件流已中断", "正在自动恢复…")
        self.reconnect(automatic=True)
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
            self._retry.reset()
            self.control.retry()
            if self.attached():
                return
        if self._startup is not None or not self._retry.start():
            return
        self._automatic = automatic
        self.label.setText(self.LABELS["reconnecting"])
        if self._startup is None:
            from sico.service.service_startup import ServiceStartup

            self._startup = ServiceStartup(self.window.api.launch_directory)
        return
