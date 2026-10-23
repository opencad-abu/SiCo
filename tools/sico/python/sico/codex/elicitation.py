"""MCP interactions bound to their original root/child turn and RPC connection."""

import uuid
from copy import deepcopy

from ..core.contracts import identifier
from ..core.review_classes import BLOCKING
from .elicitation_schema import form_schema, response, text, web_url
from .input_requests import request_id
from .input_delivery import InputDelivery, send_controls


class Elicitations:
    def __init__(self, *, runtime, requests, index, active_work, emit, withdraw, end):
        self.runtime, self.requests, self.index = runtime, requests, index
        self.active_work, self.publish = active_work, emit
        self.withdraw, self.end_inputs = withdraw, end

    @property
    def seen(self):
        return self.requests.seen

    def emit(self, kind, payload, *, origin=None):
        if origin is None:
            origin = self.index.audits[payload["id"]]["elicitation_origin"]
        self.publish(kind, {**payload, "elicitation_origin": origin})

    def _pending(self):
        with self.runtime.lock:
            return [(key, value) for key, value in self.requests.snapshot().items()
                    if value.get("source") == "elicitation"]

    def reject(self, event, reason, *, invalid=False):
        with self.runtime.lock:
            reply = self._rejection(event, reason, invalid=invalid)
        if reply:
            send_controls([reply])

    def _rejection(self, event, reason, *, invalid=False):
        fresh = self.requests.discard(event)
        result = {"error": {"code": -32602, "message": reason}} if invalid else {
            "result": {"action": "decline"}}
        # No challenge, unvalidated form or credentials are copied into history.
        self.requests.report_ignored(reason)
        if fresh:
            return self.requests.connection, {"id": event["id"] if request_id(event.get("id"))
                                           else None, **result}

    def open(self, event, turn_id, *, child=None):
        with self.runtime.lock:
            reply = self._open(event, turn_id, child=child)
        if reply:
            send_controls([reply])

    def _open(self, event, turn_id, *, child=None):
        params = event.get("params") or {}
        rpc_id = event["id"]
        scope = self.runtime.snapshot()
        expected_thread = child.thread_id if child is not None else scope.thread_id
        expected_turn = child.turn_id if child is not None else turn_id
        if (not isinstance(params, dict) or not request_id(rpc_id)
                or params.get("threadId") != expected_thread
                or params.get("turnId") not in (None, expected_turn)
                or not scope.admitted
                or scope.origin == "startup"):
            return self._rejection(event, "交互请求不属于当前正在执行的任务", invalid=True)
        mode = params.get("mode")
        if mode not in ("form", "url"):
            return self._rejection(event, "SiCo 仅支持普通 MCP 表单与网页交互；不支持账号登录或设备验证")
        try:
            server = text(params.get("serverName"), 200)
            spec = {"mode": mode, "message": text(params.get("message"))}
            if mode == "form":
                spec["schema"] = form_schema(params.get("requestedSchema"))
            else:
                spec.update(url=web_url(params.get("url")),
                            elicitation_id=text(params.get("elicitationId"), 200))
        except (ValueError, KeyError, TypeError, OverflowError):
            return self._rejection(event, "MCP 表单或网页地址无效，或包含尚不支持的字段")
        self.requests.connect()
        if child is not None:
            self.runtime.child_current(child.thread_id, turn_id=expected_turn)
        binding = {"connection_id": self.requests.connection_id, "request_id": rpc_id,
                   # MCP leaves turnId nullable.  Keep the wire identity
                   # intact and retain the effective host/child turn in
                   # host_turn_id/child_turn_id for scope validation.
                   "thread_id": expected_thread, "turn_id": params.get("turnId"),
                   "host_turn_id": child.parent_turn_id if child else turn_id,
                   "server_name": server}
        if child is not None:
            binding.update({"child_thread_id": child.thread_id,
                            "parent_thread_id": child.parent_thread_id,
                            "child_turn_id": expected_turn})
        claim = self.requests.check(rpc_id, expected_thread, [binding, spec])
        if claim is None:
            return
        current = self.active_work()
        existing = [r for r in self.index.audits.values()
                    if r["task_id"] == scope.task_id
                    and ("elicitation" in r or "input_origin" in r)]
        if len(existing) >= 32 or len(self.requests.snapshot()) >= 4:
            return self._rejection(event, "本任务的 MCP 交互数量已达上限")
        audit_id = "a_" + uuid.uuid4().hex
        title = server + " · " + ("填写表单" if mode == "form" else "网页交互")
        if child is not None:
            title = child.policy.name + " · " + title
        origin = {"thread_id": expected_thread, "turn_id": expected_turn,
                  "task_id": scope.task_id,
                  "context": scope.context, "request": binding}
        if child is not None:
            origin["child_scope"] = self.runtime.child_scope(child)
        row = {"id": audit_id, "title": title,
               "questions": [], "recommendation": "", "rationale": spec["message"],
               "elicitation": spec, "context": scope.context,
               "task_id": scope.task_id,
               "review_class": BLOCKING,
               "work_id": current["work_id"], "stage_id": current["stage_id"],
               "evidence": [{"id": current["source"],
                             "digest": self.index.data[current["source"]]["digest"]}]}
        self.emit("prepared", row, origin=origin)
        pending = {"rpc_id": rpc_id, "binding": binding,
                                        "blocking": True, "review_class": BLOCKING,
                                        "source": "elicitation",
                                        "child_thread_id": child.thread_id if child else "",
                                        "child_turn_id": expected_turn if child else ""}
        self.requests.open(audit_id, pending,
                           lambda: self.emit("opened", {"id": audit_id, "review_class": BLOCKING,
                                                       **binding}), claim=claim)
        if audit_id in self.requests.snapshot():
            self.runtime.activity("waiting_user", "等待答复：" + row["title"])

    def current(self, audit_id, scope, *, pending=None):
        current = self.runtime.snapshot()
        self.index.sync()
        row = self.index.resolve("audit", audit_id)
        pending = pending or self.requests.snapshot().get(audit_id)
        if (not pending or pending.get("source") != "elicitation"
                or scope != {"binding": row["binding"], "context": row["context"],
                             "task_id": row["task_id"]}
                or row["task_id"] != current.task_id
                or row["context"] != current.context
                or (not pending.get("child_thread_id")
                    and pending["binding"]["host_turn_id"] != current.turn_id)
                or (not pending.get("child_thread_id")
                    and pending["binding"]["thread_id"] != current.thread_id)
                or not self.requests.is_current()
                or pending["binding"]["connection_id"] != self.requests.connection_id
                or self.requests.is_closed(pending["binding"]["thread_id"], pending["rpc_id"])
                or not current.admitted):
            raise ValueError("此交互已结束、来源已变化或不属于当前回合")
        if pending.get("child_thread_id"):
            self.runtime.child_current(pending["child_thread_id"],
                                           turn_id=pending["child_turn_id"])
        return row

    def answer(self, audit_id, value, scope, reply_id, actor):
        with self.runtime.lock:
            row = self.current(audit_id, scope)
            normalized = response(row["elicitation"], value)
            identifier(reply_id)
            if row.get("reply"):
                if row["reply"].get("response") == normalized:
                    return False
                raise ValueError("此交互已答复，不能覆盖原决定")
            if row["status"] != "pending":
                raise ValueError("此交互不再等待答复")
            self.emit("reply_received", {"id": audit_id, "reply_id": reply_id,
                      "response": normalized, "actor": text(actor, 200)})
            return True

    def url(self, audit_id, scope):
        with self.runtime.lock:
            row = self.current(audit_id, scope)
            if row["status"] != "pending" or row["elicitation"]["mode"] != "url":
                raise ValueError("此交互不再等待网页操作")
            return web_url(row["elicitation"]["url"])

    def deliver(self):
        delivered = 0
        # A resolution already queued by the reader wins over a local answer.
        poll = getattr(self.requests.connection, "poll_notifications", None)
        if self._pending() and callable(poll):
            poll()
        for key, pending in self._pending():
            with self.runtime.lock:
                delivery, replies = self._prepare_delivery(key, pending)
            send_controls(replies)
            if delivery:
                delivered += delivery.send()
        return delivered

    def _prepare_delivery(self, key, pending):
        self.index.sync()
        row = self.index.audits[key]
        if row["status"] != "answer_received":
            return None, []
        scope = {"binding": row["binding"], "context": row["context"], "task_id": row["task_id"]}
        try:
            self.current(key, scope)
            self.index.verify_evidence(row["evidence"])
        except (ValueError, OSError):
            return None, self.withdraw("来源或证据已变化，答复未交付", keys=[key])
        reply = deepcopy(row["reply"])
        delivery = InputDelivery(
            key, pending, self.requests.connection, reply["response"], self.emit,
            runtime=self.runtime, requests=self.requests, row=row,
            status=lambda: self.index.audits[key]["status"])
        # Removal and waiting projection precede the durable dispatch claim.
        consumed = self.requests.resolve(key, lambda: self.emit(
            "dispatching", {"id": key, "reply_id": reply["reply_id"]}))
        if consumed is None:
            return None, []
        try:
            self.current(key, scope, pending=consumed)
        except ValueError:
            self.emit("invalidated", {"id": key, "reason": "交付前交互已结束或来源变化，答复未发送"})
            return None, self.requests.cancellation(consumed)
        return delivery, []

    def end(self, reason, *, keys=None, send=True):
        selected = [key for key, _ in self._pending() if keys is None or key in keys]
        self.end_inputs(reason, keys=selected, send=send)

    def resolved(self, event):
        return self.requests.resolved(event)
