"""Choose a window detach or an observed project shutdown without blocking Qt."""

from PyQt5.QtCore import QObject

from .force_service_stop import FORCE_STOP, ForceServiceStop
from .receipts import DataReceipt
from .service_stop_blocked import BlockedServiceStop
from .si_prompt import CANCEL, SiConfirm

WINDOW_ONLY = "关闭窗口"
STOP_SERVICE = "结束服务"
# 关闭对话框的三个选择从左到右：[结束服务] [关闭窗口] [取消]。强制停止只在「服务」菜单里，
# 不再出现在这个对话框，避免误触。自绘对话框按注册顺序排版，这一排就是权威顺序。
ROW = ((STOP_SERVICE, STOP_SERVICE), (WINDOW_ONLY, WINDOW_ONLY), (CANCEL, CANCEL))
PARTS = (("sessions", "个会话"), ("queued", "条未开始的输入"), ("approvals", "项待答复"),
         ("jobs", "个后台作业"), ("reconcile", "项待核对"))


def pending_summary(pending):
    counts = [(getattr(pending, key), text) for key, text in PARTS if getattr(pending, key)]
    return "、".join(f"{value} {text}" for value, text in counts)


class CloseChoice(QObject):
    def __init__(self, window, *, opened=None, end=None, stop=None, finished=None):
        super().__init__(window)
        self.window = window
        self._opened = opened or (lambda: window.page.opened.values())
        self._end = end or (lambda token, done, failed:
                           window.service_connection.ending.end(token, done, failed))
        self._stop = stop or (lambda done, failed:
                             window.service_connection.stop_service(done, failed))
        self._finished = finished or window.request_quit
        self.receipt = DataReceipt(self)
        self.dialog = None
        self.queue = []
        self._busy = False
        self._attempt = 0
        self.errors = []
        self.force = ForceServiceStop(window, self._begin_force)
        self.blocked = BlockedServiceStop(window, self.continue_stop)

    @property
    def busy(self):
        return self._busy or self.force.pending or self.blocked.pending

    def _begin_force(self):
        self.blocked.close()
        self._attempt += 1
        self._busy = False
        self.queue.clear()
        self.window.service_connection.receipt.clear()

    def ask(self):
        if self.window.lifecycle.closing:
            return
        if self.dialog is not None:
            self.dialog.raise_()
            self.dialog.activateWindow()
            return
        if getattr(self.window, "service_connection", None) is None:
            self.window.request_quit()
            return
        dialog = SiConfirm("关闭 SiCo", self._question(), self.window)
        # 三个选择按 ROW 注册，排出来的顺序就是这一排的权威顺序。
        for key, text in ROW:
            dialog.add_choice(key, text, default=key == WINDOW_ONLY, escape=key == CANCEL)
        dialog.set_enabled(STOP_SERVICE, not self.busy)
        dialog.set_enabled(WINDOW_ONLY, not self.force.pending)
        if self.force.pending:
            dialog.set_default(CANCEL)
        dialog.chosen.connect(self._chosen)
        self.dialog = dialog
        dialog.open()

        def observed(result):
            if self.dialog is dialog:
                dialog.set_message(self._question(result.status.pending))

        try:
            self.receipt.watch(self.window.api.service_status(), observed, lambda _error: None)
        except (ValueError, OSError, RuntimeError):
            pass  # The window-only option remains usable without a service response.

    def _question(self, pending=None):
        text = (f"{WINDOW_ONLY}：项目服务与各会话继续运行，之后重新打开可以接着用。\n"
                f"{STOP_SERVICE}：先结束本窗口打开过的会话；收尾完成后请求停止。\n"
                "项目中其他活动会话或资源尚未收尾时，服务会保留并说明原因。\n"
                "已归档的中断记录会保留供核对，不阻止关闭服务。\n"
                f"服务异常或收尾卡住时，可从「服务」菜单选择“{FORCE_STOP}”。")
        if pending is not None:
            text += "\n项目当前：" + (pending_summary(pending) or "没有未决工作") + "。"
        return text

    def _chosen(self, key):
        dialog = self.dialog
        if dialog is None:
            return
        self.dialog = None
        self.receipt.clear()
        dialog.deleteLater()
        if key == WINDOW_ONLY:
            self.window.request_quit()
        elif key == STOP_SERVICE:
            self.stop_everything()

    def stop_everything(self):
        if self.busy or self.window.lifecycle.closing:
            return
        window = self.window
        self.continue_stop(tuple(token for token in self._opened()
                                 if token.runtime_id is not None and window.api.owns(token)))

    def continue_stop(self, tokens):
        """End exactly the captured, confirmed runtimes before retrying service stop."""
        if self.busy or self.window.lifecycle.closing:
            return
        self.blocked.close()
        self.queue = list(tokens)
        self.errors = []
        self._attempt += 1
        self._busy = True
        self.window.statusBar().showMessage("正在结束会话并停止项目服务…")
        self.end_next()

    def end_next(self):
        if self.window.lifecycle.closing or not self._busy:
            return
        if not self.queue:
            self.request_stop()
            return
        token = self.queue.pop(0)
        attempt = self._attempt
        self._end(
            token, lambda _result: self._current(attempt) and self.end_next(),
            lambda error: self._current(attempt) and self.end_failed(token, error))

    def _current(self, attempt):
        return (attempt == self._attempt and self._busy
                and not self.window.lifecycle.closing)

    def request_stop(self):
        attempt = self._attempt
        if self._current(attempt):
            self._stop(
                lambda result: self._current(attempt) and self.stopped(result),
                lambda error: self._current(attempt) and self.refused(error))

    def stopped(self, receipt):
        if receipt.status.outcome == "stop_requested":
            self._busy = False
            self._finished()
            return
        details = pending_summary(receipt.status.pending) or "未决工作"
        if self.errors:
            details += "；" + "；".join(self.errors)
        self._busy = False
        self.window.statusBar().clearMessage()
        self.blocked.open(details)

    def end_failed(self, token, error):
        self.errors.append(token.session_id + "：" + str(error))
        self.end_next()

    def refused(self, error):
        self._busy = False
        self.window.statusBar().clearMessage()
        self.window.info_app.warning("结束服务未执行", str(error)
            + "\n\n活动会话可选择“结束并归档”；收尾未确认时请核对原结束请求。"
            f"已归档记录无需再次归档；也可选择{WINDOW_ONLY}。\n"
            f"服务异常或无法完成收尾时，可从服务菜单选择“{FORCE_STOP}”。")
