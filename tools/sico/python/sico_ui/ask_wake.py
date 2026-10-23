"""Validate advisory wake hints against the service's current inbox head."""

from collections import deque

from PyQt5.QtCore import QTimer

from sico.core.ask_queue import ask_sequence
from sico.core.contracts import identifier
from .receipts import DataReceipt


class AskWake:
    def __init__(self, activation):
        self.activation = activation
        self.waiting = deque()
        self.busy = False
        self.closed = False
        self.receipt = None

    def enqueue(self, session_id, input_id, sequence):
        identifier(input_id)
        ask_sequence(sequence)
        if self.closed:
            return
        if len(self.waiting) >= 64:
            self.activation._report("Ask 激活队列已满；任务接收状态不变，请在会话中查看。")
            return
        self.waiting.append((session_id, input_id, sequence))
        self._next()

    def _next(self):
        if self.closed or self.busy or not self.waiting:
            return
        self.busy = True
        session_id, input_id, sequence = self.waiting.popleft()
        window = self.activation.window
        page = window.page
        scope = page.activation
        completed = False

        def ready(receipt):
            head = receipt.queue_head if receipt is not None else None
            if (not self.closed and page.activation == scope and receipt is not None
                    and receipt.outcome == "accepted" and not receipt.recovery_required
                    and receipt.input_id == input_id
                    and receipt.address.session.session_id == session_id
                    and receipt.ask_sequence == sequence
                    and ((head is not None and head["input_id"] == input_id
                          and head["ask_sequence"] == sequence)
                         or (head is None and receipt.execution not in
                             {"queued", "executing", "waiting_user"}))):
                self.activation._activate(session_id, finished=finished)
            else:
                finished()

        def failed(exc):
            if not self.closed and page.activation == scope:
                self.activation._report(str(exc))
            finished()

        def finished():
            nonlocal completed
            if completed:
                return
            completed = True
            self.busy = False
            QTimer.singleShot(0, self._next)

        try:
            if self.receipt is None:
                self.receipt = DataReceipt(window)
            self.receipt.watch(window.api.ask_head(session_id, input_id), ready, failed)
        except Exception as exc:
            failed(exc)

    def close(self):
        self.closed = True
        self.waiting.clear()
        if self.receipt is not None:
            self.receipt.clear()
        self.busy = False
