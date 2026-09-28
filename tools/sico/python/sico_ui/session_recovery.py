"""Continue conversations; legacy diagnostic controls remain hidden from normal menus.

The inspect/restore methods support older diagnostic clients during migration.
Remove them when those clients no longer use paused recovery.
"""

import uuid

from PyQt5.QtCore import QObject, Qt, QTimer
from PyQt5.QtWidgets import (
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QVBoxLayout,
)

from .chrome import SiDialog
from .receipts import DataReceipt
from .si_prompt import CANCEL, SiConfirm


class SessionRecovery(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.receipt = DataReceipt(self)
        self.dialog = None
        self.scope = None
        self.browsing = None
        self.operations = {}
        self.action = window.session_action("核对与恢复当前会话…", self.inspect)
        self.action.setVisible(False)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(100)

    def _scope(self):
        page = self.window.page
        return page.activation, page.reviewing or page.session.session_id

    def current(self):
        return (not self.window.lifecycle.closing and self.scope == self._scope()
                and not self.window.page.opening)

    def refresh(self):
        if self.window.lifecycle.closing or self.scope is not None and not self.current():
            self.receipt.clear()
            self.dismiss()
        if self.browsing is not None:
            self._open_browsed()
        self.action.setEnabled(not self.window.lifecycle.closing and not self.window.page.opening
                               and self.receipt.pending is None)
        if self.window.lifecycle.closing:
            self.timer.stop()

    def dismiss(self):
        if self.dialog is not None:
            dialog, self.dialog = self.dialog, None
            dialog.close()
            dialog.deleteLater()

    def failed(self, error):
        if self.current():
            self.window.statusBar().clearMessage()
            self.window.info_app.warning("恢复结果未确认", "请核对原请求：" + str(error))

    def recover(self, session_id):
        if self.window.lifecycle.closing:
            return
        self.receipt.clear()
        self.dismiss()
        self.window.page.continue_session(session_id)

    def _open_browsed(self):
        """Open the dialog once the record a row-level recovery asked for is on screen."""

        page = self.window.page
        if page.reviewing is None:
            # 浏览失败或被切走：本次行恢复结束，下一次右键重新开始。
            self.browsing = None
        elif not page.opening and page.reviewing == self.browsing:
            self.browsing = None
            self.inspect()

    def inspect(self):
        if self.window.lifecycle.closing or self.window.page.opening:
            return
        self.scope = self._scope()
        self.window.statusBar().showMessage("正在读取持久恢复记录…")
        key = self.scope[1]
        self.receipt.watch(self.window.api.recovery.inspect(key, self.operations.get(key)),
                           self.present, self.failed)

    def present(self, view):
        if not self.current():
            return
        self.dismiss()
        dialog = SiDialog("会话恢复与核对", self.window, ("close",))
        self.dialog = dialog
        dialog.setModal(False)
        layout = QVBoxLayout()
        layout.setContentsMargins(12, 12, 12, 12)
        dialog.content_layout().addLayout(layout)
        status = QLabel({"ready": "可以按捕获配置恢复为暂停会话。",
            "waiting_credentials": "等待补充此会话的模型凭据；凭据仅用于本次恢复。",
            "live": "会话已在当前服务中。遗留输入需逐项核对，再明确恢复队列。",
            "ended": "会话已删除，或原结束操作尚未确认释放资源；暂不能恢复。",
            "archived_readonly": "此会话为迁移历史，保持只读。请新建会话继续工作。",
            "legacy_readonly": "此历史缺少配置快照；保持只读。"}[view["state"]])
        status.setTextFormat(Qt.PlainText)
        status.setWordWrap(True)
        layout.addWidget(status)
        task = QLabel("原任务：" + (view["task_status"] or "无"))
        task.setTextFormat(Qt.PlainText)
        layout.addWidget(task)
        inputs = QListWidget()
        for item in view["inputs"]:
            inputs.addItem(item["input_id"] + " · " + item["status"])
        layout.addWidget(inputs)
        if view["operation"]:
            label = QLabel("原恢复请求已持久记录；查询本身不会再次恢复。")
            layout.addWidget(label)
        if view["state"] in {"ready", "waiting_credentials"}:
            key = QLineEdit()
            key.setEchoMode(QLineEdit.Password)
            key.setPlaceholderText(view["credential"] or "此会话无需模型凭据")
            key.setEnabled(bool(view["credential"]))
            key.setMaxLength(8192)
            layout.addWidget(key)
            button = QPushButton("恢复为暂停会话")
            layout.addWidget(button)
            button.clicked.connect(lambda: self.restore(view, key))
        elif view["state"] == "live":
            attach = QPushButton("只读关联此会话")
            layout.addWidget(attach)
            attach.clicked.connect(lambda: self.attach(view["session_id"]))
            token = self.window.api.session(view["session_id"])
            writable = (self.window.page.reviewing is None
                        and self.window.api.can_control(token))
            for operation, text in (("recover_input", "选择所选未开始输入"),
                                    ("abandon_input", "放弃所选输入…")):
                button = QPushButton(text)
                button.setEnabled(writable)
                layout.addWidget(button)
                button.clicked.connect(lambda _checked=False, op=operation:
                                       self.select(view, inputs.currentRow(), op))
            for operation, text in (("acknowledge_interrupted", "确认放弃原中断任务…"),
                                    ("resume", "明确恢复队列")):
                button = QPushButton(text)
                button.setEnabled(writable)
                layout.addWidget(button)
                button.clicked.connect(lambda _checked=False, op=operation: self.task(view, op))
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.show()

    def restore(self, view, field):
        if not self.current():
            return
        credentials = {view["credential"]: field.text()} if view["credential"] else {}
        field.clear()
        operation = uuid.uuid4().hex
        self.operations[view["session_id"]] = operation
        self.dismiss()
        self.window.statusBar().showMessage("正在准备暂停会话…")
        self.receipt.watch(self.window.api.recovery.restore(view, operation, credentials),
                           self.present, self.failed)

    def attach(self, key):
        if not self.current():
            return
        self.dismiss()
        self.window.page.activate_recovered(key)

    def _command(self, view, operation, *args, **kwargs):
        if not self.current():
            return
        token = self.window.api.session(view["session_id"])
        if self.window.page.reviewing is not None or not self.window.api.can_control(token):
            return
        self.dismiss()
        self.receipt.watch(self.window.api.command(token, operation, *args, **kwargs),
                           lambda _result: self.inspect(), self.failed)

    def confirm(self, callback):
        self.dismiss()
        dialog = SiConfirm("确认放弃",
                           "放弃只结束本地待处理记录；不会撤销已发生或结果未知的外部操作。",
                           self.window)
        dialog.add_choice("accept", "放弃", default=True)
        dialog.add_choice("cancel", CANCEL, escape=True)
        self.dialog = dialog
        dialog.chosen.connect(lambda key: callback()
                              if key == "accept" and self.current() else None)
        dialog.open()

    def select(self, view, index, operation):
        if not 0 <= index < len(view["inputs"]):
            return
        key = view["inputs"][index]["input_id"]
        def run():
            self._command(view, operation, key)
        self.confirm(run) if operation == "abandon_input" else run()

    def task(self, view, operation):
        def run():
            self._command(view, operation, expected_task=view["task_id"])
        self.confirm(run) if operation == "acknowledge_interrupted" else run()
