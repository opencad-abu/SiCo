"""Poll one replaceable data receipt; callbacks always run on the Qt thread."""

from concurrent.futures import CancelledError

from PyQt5.QtCore import QObject, QTimer

from sico.service.diagnostics import diagnostics


def completed_result(future):
    if not future.done():
        raise RuntimeError("Data is not published yet")
    return future.result()


class DataReceipt(QObject):
    def __init__(self, parent):
        super().__init__(parent)
        self.pending = None
        self.timer = QTimer(self)
        self.timer.setInterval(25)
        self.timer.timeout.connect(self.poll)
        # destroyed(QObject*) carries a payload; compiled ``clear`` cannot rely
        # on PyQt argument reduction, so drop it here.
        parent.destroyed.connect(lambda *_args: self.clear())

    def watch(self, future, success, failure):
        self.clear()
        self.pending = (future, success, failure)
        self.timer.start()

    def clear(self):
        if self.pending:
            discard = getattr(self.pending[0], "discard", None)
            if discard is not None:
                discard()
            self.pending[0].cancel()
        self.pending = None
        self.timer.stop()

    def poll(self):
        if self.pending is None or not self.pending[0].done():
            return
        future, success, failure = self.pending
        self.pending = None
        self.timer.stop()
        try:
            result = future.result()
            success(result)
        except CancelledError:
            return
        except Exception as exc:
            try:
                failure(exc)
            except Exception as callback_error:
                diagnostics.submit(__name__, "Data receipt callback failed", callback_error)
