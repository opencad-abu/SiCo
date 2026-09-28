"""One end-operation registry shared by session pages and the independent home."""

from PyQt5.QtCore import QObject, QTimer

from .session_end_operation import EndOperation


class SessionEndGroup(QObject):
    TEXT = {"authorizing": "正在准备结束会话…", "unknown": "会话结束结果未确认",
            "closing": "正在结束会话…", "ended": "会话已结束",
            "refused": "结束会话未执行", "needs_reconcile": "会话结束结果待核对"}

    DETAILS = {"unknown": "结束结果未确认，请核对原请求",
               "needs_reconcile": "会话结束仍有未决结果，请核对原操作",
               "ended": "会话已结束，记录已保留"}

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.operations = {}
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(300)

    @property
    def pending(self):
        return any(operation.pending for operation in self.operations.values())

    def close(self):
        self.timer.stop()
        for operation in self.operations.values():
            operation.close()

    def refresh(self):
        if self.window.lifecycle.closing:
            self.close()
            return False
        for operation in tuple(self.operations.values()):
            operation.poll()
        return True

    def _changed(self):
        self.refresh()

    def end(self, token, settled, failed):
        """Join the original intent; complete only after release, independently of page switches."""
        operation = self.operations.get(token)
        if operation is None or operation.state == "refused":
            operation = EndOperation(self, self.window.api, token)
            self.operations[token] = operation
            operation.changed.connect(self._changed)
            fresh = True
        else:
            fresh = False

        def complete(result):
            operation.finished.disconnect(complete)
            if self.window.lifecycle.closing:
                return
            if result.state == "ended":
                settled(result)
            else:
                failed(result.error or self.DETAILS.get(result.state, self.TEXT[result.state]))

        if operation.state == "ended":
            settled(operation)
            return
        operation.finished.connect(complete)
        if fresh:
            operation.start()
        elif not operation.pending:
            operation.observe()
