"""Review remaining service sessions before explicitly extending the stop scope."""

from dataclasses import dataclass

from PyQt5.QtCore import QObject, QTimer
from PyQt5.QtWidgets import QAbstractItemView, QHeaderView, QTreeWidget, QTreeWidgetItem

from .receipts import DataReceipt
from .si_prompt import CANCEL, SiForm

CONFIRM_STOP = "结束遗留会话并继续停止"
ACTIVITY = {"idle": "空闲", "active": "正在执行", "pending": "挂起／需处理"}
CONNECTION = {"detached": "原窗口已断开", "recoverable": "原窗口已断开",
              "available": "无窗口持有控制权", "occupied": "其他窗口正在使用",
              "owned": "本窗口连接", "closing": "正在释放连接"}


@dataclass(frozen=True)
class RemainingSession:
    token: object
    name: str
    activity: str
    connection: str

    @property
    def eligible(self):
        return (self.activity in ACTIVITY
                and self.connection in {"detached", "recoverable", "available", "owned"})


class BlockedServiceStop(QObject):
    def __init__(self, window, resume):
        super().__init__(window)
        self.window, self.resume = window, resume
        self.dialog = None
        self.rows = []
        self.receipt = DataReceipt(self)
        self._epoch = 0
        self._validating = False
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self._lifetime)

    @property
    def pending(self):
        return self._validating

    def _lifetime(self):
        if self.window.lifecycle.closing:
            self.close()

    def open(self, details):
        self.close()
        if self.window.lifecycle.closing:
            return
        self.details = details
        self.rows = []
        self.timer.start()
        dialog = self.dialog = SiForm("项目服务暂未停止", self.window)
        dialog.set_message(details + "\n正在查询剩余会话的运行和窗口连接状态…")
        self.table = QTreeWidget()
        self.table.setHeaderLabels(["会话", "运行状态", "窗口连接", "本次处理"])
        self.table.setRootIsDecorated(False)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.header().setSectionResizeMode(0, QHeaderView.Stretch)
        for column in (1, 2, 3):
            self.table.header().setSectionResizeMode(column, QHeaderView.ResizeToContents)
        dialog.add_field(self.table)
        dialog.add_choice("finish", CONFIRM_STOP, danger=True)
        dialog.add_choice("refresh", "刷新")
        dialog.add_choice(CANCEL, CANCEL, default=True, escape=True)
        dialog.set_enabled("finish", False)
        dialog.body_layout.addLayout(dialog.actions)
        dialog.resize(800, 380)
        dialog.chosen.connect(self._chosen)
        dialog.setMinimumWidth(720)
        dialog.open()
        self._watch(self.window.api.catalog.refresh, self._inventory, self._failed)

    def _watch(self, request, success, failure):
        epoch = self._epoch
        def current(callback, result):
            if epoch == self._epoch and not self.window.lifecycle.closing:
                callback(result)
        try:
            self.receipt.watch(request(), lambda row: current(success, row),
                               lambda error: current(failure, error))
        except (ValueError, RuntimeError, OSError) as exc:
            current(failure, exc)

    def _inventory(self, tokens):
        self._remaining = list(tokens)
        self._next()

    def _next(self):
        if not self._remaining:
            self._render()
            return
        api = self.window.api
        token = self._remaining.pop(0)
        try:
            api.handle(token)
            name = api.display_name(token)
            name = f"{name}\n{token.session_id}" if name and name != token.session_id else token.session_id
            activity = api.activity(token)
            if api.is_closing(token):
                self.rows.append(RemainingSession(token, name, "closing", "closing"))
                self._next()
                return
        except (ValueError, RuntimeError):
            self.rows.append(RemainingSession(token, token.session_id, "unknown", "unknown"))
            self._next()
            return
        def observed(view):
            self.rows.append(RemainingSession(token, name, activity, view.state))
            self._next()
        def failed(_error):
            self.rows.append(RemainingSession(token, name, activity, "unknown"))
            self._next()
        self._watch(lambda: api.control.request(token), observed, failed)

    def _render(self):
        for row in self.rows:
            activity = "正在收尾" if row.activity == "closing" else ACTIVITY.get(row.activity, "状态未确认")
            connection = CONNECTION.get(row.connection, "状态未确认")
            item = QTreeWidgetItem([row.name, activity, connection,
                                   "确认后结束" if row.eligible else "保留"])
            item.setToolTip(0, row.name)
            self.table.addTopLevelItem(item)
        count = sum(row.eligible for row in self.rows)
        self.dialog.set_message(self.details + "\n\n"
            + (f"确认后结束表中标记的 {count} 个会话并保留历史，再尝试停止服务。\n"
               "正在执行的任务会停止，未执行队列保留；已发出的操作仍可能需要核对。\n"
               if count else "当前没有可在此结束的遗留会话。\n")
            + "其他窗口正在使用、正在收尾或状态未确认的会话会保留。\n"
            "确认前会重新核验；新出现的会话不会自动加入。")
        self.dialog.set_enabled("finish", bool(count))
        self.dialog.adjustSize()

    def _failed(self, error):
        self._validating = False
        self.window.statusBar().clearMessage()
        if self.dialog is not None:
            self.dialog.set_message(self.details + "\n剩余会话状态查询失败：" + str(error)
                                    + "\n没有结束任何遗留会话；可刷新后重试。")
            self.dialog.set_enabled("finish", False)
        else:
            self.window.info_app.warning("遗留会话尚未结束", str(error))

    def _chosen(self, key):
        selected = tuple(row for row in self.rows if row.eligible)
        self.close()
        if self.window.lifecycle.closing:
            return
        if key == "refresh":
            self.open(self.details)
        elif key == "finish" and selected:
            self._validating = True
            self.timer.start()
            self.window.statusBar().showMessage("正在重新核对遗留会话…")
            self._watch(self.window.api.catalog.refresh,
                        lambda tokens: self._validated(selected, tokens), self._failed)

    def _validated(self, selected, tokens):
        live = {token.session_id: token for token in tokens}
        api = self.window.api
        remaining = []
        for row in selected:
            token = live.get(row.token.session_id)
            if token is None:
                continue  # Already ended; never replace it with another runtime.
            if (token != row.token or not api.owns(token) or api.is_closing(token)
                    or api.activity(token) != row.activity):
                self.open(self.details + "\n会话状态已变化，请核对最新列表后重新确认。")
                return
            remaining.append(token)
        self.close()
        # The existing end operation rechecks control and runtime on the server.
        self.resume(tuple(remaining))

    def close(self):
        self._epoch += 1
        self._validating = False
        self.receipt.clear()
        self.timer.stop()
        dialog, self.dialog = self.dialog, None
        if dialog is not None:
            dialog.chosen.disconnect(self._chosen)
            dialog.reject()
            dialog.deleteLater()
