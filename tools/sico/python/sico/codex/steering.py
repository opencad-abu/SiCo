"""One active-turn mailbox, drained exclusively by the native event consumer."""

from concurrent.futures import Future

from ..core.contracts import identifier, json_copy
from .rpc import RpcError, RpcRejected

MESSAGES = {
    "queued": "补充已保存，等待发送到当前回合",
    "sending": "正在补充当前回合",
    "accepted": "Codex 已接收当前回合的补充",
    "rejected": "补充未接收；原回合已结束、取消或来源已变化",
    "unconfirmed": "补充结果未确认；请核对当前对话，不会自动重发",
}


class TurnSteering:
    def __init__(self, backend, records):
        self.backend = backend
        self.turn_id = None
        self.pending = None
        self.rows, self.receipts = {}, {}
        for event in records:
            if event["kind"].startswith("codex.steer.") and "id" in event["payload"]:
                self.rows[event["payload"]["id"]] = event["payload"]

    def recover(self):
        # A crash never restores this mailbox or resends an input. The last
        # durable state distinguishes definitely unsent from possibly sent.
        self.clear_scope()
        for key, row in list(self.rows.items()):
            if row["status"] in {"queued", "sending"}:
                status = "rejected" if row["status"] == "queued" else "unconfirmed"
                if row["scope"]["task_id"] == self.backend.state.task.get("id"):
                    self.record(key, status)

    def identity(self):
        if not self.turn_id:
            return None
        return {"thread_id": self.backend.thread_id, "turn_id": self.turn_id,
                "task_id": self.backend.state.task.get("id"),
                "context": self.backend.state.context.record()}

    def open(self, turn_id):
        with self.backend.lock:
            if self.turn_id or self.pending:
                raise ValueError("A steering turn is already open")
            self.turn_id = turn_id
            self.backend.state.task.update(
                steer_turn=turn_id, steer_thread=self.backend.thread_id, steer_blocked=False,
            )
            self.backend._event("codex.steer.ready", {"scope": self.identity()})

    def allowed(self, scope):
        backend = self.backend
        return (scope == self.identity() and backend.active and not backend.stale
                and not backend.cancelled() and backend.state.task.get("status") == "executing"
                and not backend.state.task.get("waiting_audits")
                and not backend.state.task.get("steer_blocked"))

    def submit(self, text, scope, request_id):
        identifier(request_id)
        backend = self.backend
        if (not isinstance(text, str) or not text.strip() or "\0" in text
                or len(text) > min(16000, backend.config.context_chars // 2)):
            raise ValueError("请输入有效且未超过长度限制的补充内容")
        with backend.lock:
            old = self.rows.get(request_id)
            if old:
                if old["scope"] != scope or old["text"] != text:
                    raise ValueError("补充编号与原始内容冲突")
                if request_id in self.receipts:
                    return self.receipts[request_id]
                receipt = Future()
                receipt.set_result(json_copy(old))
                return receipt
            if not self.allowed(scope) or self.pending:
                raise ValueError("当前回合已变化、正在结束或有待答问题；补充未发送，草稿保留")
            if sum(r["scope"]["task_id"] == scope["task_id"] for r in self.rows.values()) >= 32:
                raise ValueError("当前任务的补充次数已达上限，请等待任务结束")
            self.rows[request_id] = {"id": request_id, "scope": json_copy(scope), "text": text}
            receipt = self.receipts[request_id] = Future()
            try:
                backend.state.task["steer_blocked"] = True
                self.record(request_id, "queued")  # Durable before any native send.
            except Exception:
                self.rows.pop(request_id, None)
                self.receipts.pop(request_id, None)
                backend.state.task["steer_blocked"] = False
                raise
            self.pending = request_id
            return receipt

    def record(self, key, status):
        row = {**self.rows[key], "status": status, "message": MESSAGES[status]}
        try:
            self.backend._event("codex.steer." + status, row)
        except Exception:
            # Never strand a UI receipt or resend after a journal failure.
            # Only a durable sending record permits the native RPC to begin.
            if status != "queued":
                fallback = "unconfirmed" if self.rows[key]["status"] == "sending" else "rejected"
                self.rows[key] = {**row, "status": fallback, "persisted": False,
                                  "message": "补充回执未能保存；" + MESSAGES[fallback]}
                self.pending = None
                self.backend.state.task["steer_blocked"] = True
                self.resolve(key)
            raise
        self.rows[key] = row
        if status in {"accepted", "rejected", "unconfirmed"}:
            self.resolve(key)

    def resolve(self, key):
        receipt = self.receipts.get(key)
        if receipt is not None and not receipt.done():
            receipt.set_result(json_copy(self.rows[key]))

    def clear_scope(self):
        for key in ("steer_turn", "steer_thread", "steer_blocked"):
            self.backend.state.task.pop(key, None)

    def end(self, turn_id=None):
        with self.backend.lock:
            if not self.turn_id or (turn_id is not None and self.turn_id != turn_id):
                return
            scope = self.identity()
            self.turn_id = None
            self.clear_scope()
            if self.pending:
                key, self.pending = self.pending, None
                status = "unconfirmed" if self.rows[key]["status"] == "sending" else "rejected"
                self.record(key, status)
            self.backend._event("codex.steer.closed", {"scope": scope})

    def dispatch(self):
        """Called only by the active turn consumer, never the command worker."""
        backend = self.backend
        with backend.lock:
            key = self.pending
            if not key:
                return
            row = self.rows[key]
            backend.state.task["steer_blocked"] = False
            if not self.allowed(row["scope"]):
                self.pending = None
                self.record(key, "rejected")
                return
            backend.state.task["steer_blocked"] = True
            self.record(key, "sending")
        try:
            result = backend.runtime.rpc.request("turn/steer", {
                "threadId": row["scope"]["thread_id"],
                "expectedTurnId": row["scope"]["turn_id"],
                "clientUserMessageId": key,
                "input": [{"type": "text", "text": row["text"]}],
            }, timeout=5)
            status = ("accepted" if isinstance(result, dict)
                      and result.get("turnId") == row["scope"]["turn_id"] else "unconfirmed")
        except RpcRejected:
            status = "rejected"
        except (RpcError, OSError, ValueError, TypeError):
            status = "unconfirmed"
        with backend.lock:
            # close/end may settle the mailbox while an RPC is in flight.
            # Its late reply must not reopen the turn or change the receipt.
            if self.pending != key or self.identity() != row["scope"]:
                return
            self.pending = None
            backend.state.task["steer_blocked"] = status == "unconfirmed"
            self.record(key, status)
