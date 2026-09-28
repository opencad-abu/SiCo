"""Explicit emergency service-stop confirmation and asynchronous result presentation."""

from PyQt5.QtCore import QObject

from .receipts import DataReceipt
from .si_prompt import CANCEL, SiConfirm

# 服务菜单里的入口；对话框按钮统一用短动作名，触发按钮与确认按钮同名。
FORCE_STOP = "强制停止"


class ForceServiceStop(QObject):
    def __init__(self, window, begin):
        super().__init__(window)
        self.window, self.begin = window, begin
        self.dialog = None
        self.pending = False
        self.receipt = DataReceipt(self)

    def confirm(self, _checked=False):
        if self.pending or self.window.lifecycle.closing:
            return
        if self.dialog is not None:
            self.dialog.raise_()
            self.dialog.activateWindow()
            return
        dialog = SiConfirm("强制停止 Agent Service",
                           "强制停止当前工程的 Agent Service？\n\n"
                           "此工程的所有会话都会断开，正在处理的任务可能中断；其他工程不受影响。\n"
                           "会话记录和待核对证据会保留，重新打开 SiCo 可启动服务；请求不会自动重发。\n"
                           "已派发到 Virtuoso 或 LSF 的操作可能继续执行，结果仍需核对。",
                           self.window)
        dialog.add_choice(FORCE_STOP, FORCE_STOP, danger=True)
        dialog.add_choice(CANCEL, CANCEL, default=True, escape=True)
        self.dialog = dialog

        def chosen(key):
            self.dialog = None
            dialog.deleteLater()
            if key == FORCE_STOP and not self.window.lifecycle.closing:
                self.start()
        dialog.chosen.connect(chosen)
        dialog.open()

    def start(self):
        if self.pending or self.window.lifecycle.closing:
            return
        self.pending = True
        self.begin()
        self.window.statusBar().showMessage("正在强制停止 Agent Service…")
        try:
            self.receipt.watch(self.window.api.force_stop_service(), self.stopped, self.failed)
        except (ValueError, OSError, RuntimeError) as exc:
            self.failed(exc)

    def stopped(self, _result):
        self.pending = False
        if not self.window.lifecycle.closing:
            self.window.request_quit()

    def failed(self, error):
        self.pending = False
        if not self.window.lifecycle.closing:
            self.window.statusBar().clearMessage()
            self.window.info_app.error("强制停止未完成", str(error))
