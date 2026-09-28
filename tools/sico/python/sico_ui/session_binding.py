"""Qt adapter for cursor-based session events with explicit rendering callbacks."""

from concurrent.futures import CancelledError

from PyQt5.QtCore import QObject, QTimer

from .event_batches import BatchDelivery, coalesce


class SessionBinding(QObject):
    REPLAY_LIMIT = 64
    REPLAY_BUDGET = BatchDelivery.BUDGET
    REPLAY_MESSAGES = 200
    COALESCED_KINDS = ("model.status", "router.status")

    def __init__(self, parent, session, *, api, page, presentation, lifecycle,
                 receive, update, caught_up, refresh_status, refresh_cursor,
                 flush, finish_close, stream_failed, released, prepared=None):
        super().__init__(parent)
        self.api, self.page = api, page
        self.presentation, self.lifecycle = presentation, lifecycle
        self.receive, self.update = receive, update
        self.caught_up, self.refresh_status = caught_up, refresh_status
        self.refresh_cursor, self.flush = refresh_cursor, flush
        self.finish_close, self.stream_failed = finish_close, stream_failed
        self.released = released
        self.session = api.handle(session)
        if prepared is not None and not api.replay_matches(prepared, self.session, page.activation):
            raise ValueError("会话回放准备结果已失效")
        self.delivery = BatchDelivery(api, prepared.stream) if prepared is not None else None
        self.cursor = self.delivery.cursor if self.delivery is not None else None
        self.committed_sequence = prepared.committed_sequence if prepared is not None else 0
        self.version = -1
        self.failed = False
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self.poll)
        self.timer.start()
        if self.delivery is not None:
            # A replaced binding can be collected before its DeferredDelete event.
            # The destroyed callback must not keep the dying QObject in a GC cycle.
            self.destroyed.connect(lambda *_args, delivery=self.delivery: delivery.close())

    coalesce = staticmethod(coalesce)

    def dispose(self):
        if self.delivery is not None:
            self.delivery.close()

    def _snapshot(self, state, contexts):
        self.version = state["version"]
        self.update(state, contexts=contexts)

    def poll(self):
        if self.lifecycle.closing:
            self.refresh_cursor()
            if self.lifecycle.ready():
                self.timer.stop()
                self.finish_close()
            return
        if self.failed or self.cursor is None or self.page.opening:
            self.refresh_cursor()
            return
        try:
            complete = self.delivery.apply(self.receive, self._snapshot)
        except (CancelledError, ValueError, KeyError, TypeError, RuntimeError):
            self.fail()
            return
        if complete and self.cursor.sequence >= self.committed_sequence:
            self.caught_up()
        if self.presentation.state is not None:
            self.refresh_status()
        self.refresh_cursor()
        self.flush()
        self.timer.setInterval(10 if not self.delivery.caught_up else 50)
        for target_id in self.api.released_targets(self.session):
            self.released(target_id)
        if self.lifecycle.ready():
            self.timer.stop()
            self.finish_close()

    def begin_close(self):
        self.lifecycle.request()

    def close_grace_expired(self):
        return self.lifecycle.expired()

    def fail(self):
        self.failed = True
        self.dispose()
        self.caught_up()
        self.api.interrupt(self.session)
        self.stream_failed()
        self.refresh_cursor()
