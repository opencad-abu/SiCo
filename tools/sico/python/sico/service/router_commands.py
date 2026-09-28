"""Session-scoped router queries with independently owned status publication."""
from ..core.contracts import NeedsReconcile
from .router_receipt import latest_router_request, router_receipt_view, validate_router_receipt
from .router_status import router_status


class SessionRouter:
    def __init__(self, journal, *, lock, execution, broker, changed):
        self.journal = journal
        self._lock = lock
        self.execution = execution
        self.broker = broker
        self.changed = changed
        self._context = None
        self._status = None

    def snapshot(self, context):
        return self._status if self._context is context else None

    def poll_router_status(self):
        """Publish bounded local routing state from the metadata worker only."""
        if not self._lock.acquire(blocking=False):
            return
        try:
            context, broker = self.execution().current, self.broker()
        finally:
            self._lock.release()
        read = getattr(broker, "router_snapshot", None)
        if not callable(read):
            return
        status = router_status(read(context), context, self.execution().session_id)
        if not self._lock.acquire(blocking=False):
            return
        try:
            if context is not self.execution().current or broker is not self.broker():
                return
            if self._context is not context or status != self._status:
                self._context, self._status = context, status
                self.changed()
        finally:
            self._lock.release()

    def cancel_router_request(self, request_id, *, context):
        """Cancel one routed request using the session and target captured by the UI."""
        with self._lock:
            if self.execution().closing or self.execution().fault or self.execution().shutdown:
                raise ValueError("Session is closing or unavailable")
            if context.record() != self.execution().current.record():
                raise ValueError("Request target has changed")
            broker = self.broker()
        if broker is None:
            raise ValueError("Session has no Virtuoso router")
        return broker.cancel_request(context, request_id, session_id=self.execution().session_id)

    def router_receipt(self, *, context=None, formatted=False):
        """Read one exact source's latest receipt; slow work never holds the session lock."""
        with self._lock:
            context = context or self.execution().current
            if self.execution().closing or self.execution().shutdown or context != self.execution().current:
                raise ValueError("回执查询来源已变化或会话已结束")
            current, broker = self.execution().current, self.broker()
        read = getattr(broker, "request_receipt", None)
        if not callable(read):
            raise ValueError("当前连接不支持查询路由回执")
        request = latest_router_request(self.journal.events(), context, self.execution().session_id)
        try:
            receipt = read(context, request["request_id"], session_id=self.execution().session_id)
        except NeedsReconcile:
            archive = getattr(broker, "archived_receipt", None)
            if not callable(archive):
                raise
            receipt = archive(self.journal.root.parents[2], context, request["request_id"],
                              session_id=self.execution().session_id)
        validate_router_receipt(receipt, request, context, self.execution().session_id)
        result = (router_receipt_view(receipt, context, self.execution().session_id, self.execution().runtime_id)
                  if formatted else receipt)
        with self._lock:
            if (self.execution().closing or self.execution().shutdown or self.execution().current is not current
                    or self.broker() is not broker):
                raise ValueError("回执查询来源已变化或会话已结束")
        return result
