"""Present bootstrap progress and credentials while the main Qt loop keeps running."""

from PyQt5.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from sico import PRODUCT_NAME

from .chrome import SiDialog
from .credentials import CredentialCancelled, credential_environment
from .si_prompt import notice
from .lifecycle import DesktopControl
from .quick_input import QuickInput
from .receipts import DataReceipt
from .window import AssistantWindow


class StartupDialog(SiDialog):
    """Bootstrap progress in the same frameless chrome as every other window."""

    canceled = pyqtSignal()

    def __init__(self):
        super().__init__(title=PRODUCT_NAME)
        self.setWindowModality(Qt.NonModal)
        self.setFixedWidth(420)
        layout = QVBoxLayout()
        self.content_layout().addLayout(layout)
        self.status = QLabel(StartupView.LABELS["registration"])
        self.status.setObjectName("startupStatus")
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.instructions = QPlainTextEdit()
        self.instructions.setReadOnly(True)
        self.instructions.setMaximumHeight(210)
        self.instructions.hide()
        layout.addWidget(self.instructions)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)  # 启动阶段没有可量化的进度，只表示“还在进行”
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(12)
        layout.addWidget(self.bar)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel = QPushButton("取消")
        self.cancel.setAutoDefault(False)
        self.cancel.setToolTip("取消启动并结束 Silicon Copilot")
        self.cancel.clicked.connect(self.reject)
        buttons.addWidget(self.cancel)
        layout.addLayout(buttons)

    def setLabelText(self, text):
        """QProgressDialog-compatible label update used by StartupView."""
        self.status.setText(text)

    def reject(self, _checked=False):
        super().reject()
        self.canceled.emit()


class StartupView(QObject):
    LABELS = {
        "registration": "正在等待工程连接…",
        "bridge": "正在连接工程…",
        "context": "正在确认设计目标…",
        "history": "正在加载会话记录…",
        "config": "正在加载模型配置…",
        "credentials": "等待输入 API Key…",
        "provider": "正在准备模型配置…",
        "session": "正在准备会话…",
        "discovery": "正在发现或启动项目服务…",
        "attachment": "正在关联服务会话…",
        "ready": "正在打开会话…",
    }

    def __init__(self, startup, reader, notice, *, quit_application=None, shutdown_guard=None):
        super().__init__()
        self.startup = startup
        self.notice = notice or (lambda *_args, **_kwargs: None)
        self.quit_application = quit_application
        self.shutdown_guard = shutdown_guard
        self.exit_code = 0
        self.window = None
        self.progress = StartupDialog()
        self.progress.setLabelText(self.LABELS[startup.phase])
        self.control = DesktopControl(
            reader, notices=notice, registration=startup.start, startup_shutdown=self._cancel,
            shutdown_guard=shutdown_guard,
        )
        self.control.startup_window = self.progress
        self.progress.canceled.connect(self.control.shutdown)
        self.configuration = DataReceipt(self)
        self.configuration.watch(startup.configuration, self._credential, self._failed)
        self.ready = DataReceipt(self)
        self.ready.watch(startup.ready, self._ready, self._failed)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._progress)
        self.timer.start(50)
        QTimer.singleShot(0, self.show)

    def show(self):
        if not self.control.closing:
            self.progress.show()

    def _progress(self):
        self.progress.setLabelText(self.LABELS[self.startup.phase])
        instructions = getattr(self.startup, "instructions", "")
        if instructions and self.progress.instructions.toPlainText() != instructions:
            self.progress.instructions.setPlainText(instructions)
            self.progress.instructions.show()
            self.progress.adjustSize()

    def _dismiss(self):
        self.timer.stop()
        self.control.startup_window = None
        self.progress.blockSignals(True)
        self.progress.close()

    def _cancel(self):
        if self.window is not None and not self.window.lifecycle.closing:
            self.window.request_quit()
        self.startup.close()
        self.configuration.clear()
        self.ready.clear()
        self._dismiss()
        if self.quit_application is not None:
            self.quit_application()

    def _credential(self, key):
        self.control.poll()
        if not key or self.control.closing:
            return
        try:
            environment = credential_environment(key)
        except CredentialCancelled:
            self.control.shutdown()
            return
        self.control.poll()
        if not self.control.closing:
            self.startup.provide_credential(environment[key])

    def _failed(self, error):
        self.control.poll()
        if self.control.closing:
            return
        self.exit_code = 2
        self.startup.close()
        self.configuration.clear()
        self.ready.clear()
        self._dismiss()
        # 没有窗口可用，也没有 InfoApp 宿主：用一个自绘的单按钮提示停下来。
        notice(None, PRODUCT_NAME + " " + getattr(error, "title", "启动失败"), str(error))
        self.control.shutdown()

    def _ready(self, result):
        self.control.poll()
        if self.control.closing:
            return
        window = AssistantWindow(
            result.frontend, persistent=result.persistent, notice=self.notice,
        )
        self.window = window
        from .service_connection import attach_service_connection

        attach_service_connection(window)
        window.lifecycle.attach_guard(self.shutdown_guard)
        if self.quit_application is not None:
            window.closed.connect(self.quit_application)
        window.setWindowTitle(window.windowTitle() + " / " + window.page.session.session_id[:8])
        if result.attach_targets:
            window.quick_input = QuickInput(window, self.notice)
        self.control.attach(window)
        self._dismiss()
        if result.initial_text:
            window.input.setPlainText(result.initial_text)
        window.restore()
        self._initial_timer = QTimer(self)
        initial, activation = window.page.session, window.page.activation

        def initialize():
            if (self.control.closing or window.lifecycle.closing or window.page.session != initial
                    or window.page.activation != activation):
                self._initial_timer.stop()
            elif not window.page.opening:
                self._initial_timer.stop()
                if window.binding.failed or window.binding.cursor is None:
                    return
                if result.initialize:
                    window.initialize_context()

        self._initial_timer.timeout.connect(initialize)
        self._initial_timer.start(25)

    def close(self):
        self.control.closing = True
        if hasattr(self, "_initial_timer"):
            self._initial_timer.stop()
        self.configuration.clear()
        self.ready.clear()
        self._dismiss()
        self.control.close()
        if self.window is not None:
            self.window.api.close_desktop()
        self.startup.close()
