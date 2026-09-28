"""MCP interactions bound to their original root/child turn and RPC connection."""

import uuid
from copy import deepcopy

from ..core.contracts import identifier
from .elicitation_schema import form_schema, response, text, web_url
from .input_requests import request_id
from .rpc import RpcError


class Elicitations:
    def __init__(self, audit):
        self.audit, self.loop = audit, audit.loop
        self.connection = None
        self.connection_id = ""

    @property
    def seen(self):
        return self.audit.requests.seen

    def connect(self):
        self.audit.requests.connect()
        self.connection = self.audit.requests.connection
        self.connection_id = self.audit.requests.connection_id

    def emit(self, kind, payload, *, origin=None):
        if origin is None:
            origin = self.audit.index.audits[payload["id"]]["elicitation_origin"]
        self.audit.emit(kind, {**payload, "elicitation_origin": origin})

    def _pending(self):
        with self.loop.lock:
            return [(key, value) for key, value in self.audit.pending.items()
                    if value.get("source") == "elicitation"]

    def _sync_waiting(self):
        self.loop.state.task["waiting_audits"] = list(self.audit.pending)

    def reject(self, event, reason, *, invalid=False):
        fresh = self.audit.requests.discard(event)
        result = {"error": {"code": -32602, "message": reason}} if invalid else {
            "result": {"action": "decline"}}
        if fresh:
            self.loop.runtime.rpc.send({"id": event["id"] if request_id(event.get("id"))
                                        else None, **result})
        # No challenge, unvalidated form or credentials are copied into history.
        self.loop._event("codex.elicitation.unsupported", {"reason": reason})

    def open(self, event, turn_id, *, child=None):
        with self.loop.lock:
            params = event.get("params") or {}
            rpc_id = event["id"]
            expected_thread = child.thread_id if child is not None else self.loop.thread_id
            expected_turn = child.turn_id if child is not None else turn_id
            if (not isinstance(params, dict) or not request_id(rpc_id)
                    or params.get("threadId") != expected_thread
                    or params.get("turnId") not in (None, expected_turn)
                    or not self.loop.active or self.loop.cancelled() or self.loop.stale
                    or self.loop.state.task.get("origin") == "startup"):
                self.reject(event, "交互请求不属于当前正在执行的任务", invalid=True)
                return
            mode = params.get("mode")
            if mode not in ("form", "url"):
                self.reject(event, "SiCo 仅支持普通 MCP 表单与网页交互；不支持账号登录或设备验证")
                return
            try:
                server = text(params.get("serverName"), 200)
                spec = {"mode": mode, "message": text(params.get("message"))}
                if mode == "form":
                    spec["schema"] = form_schema(params.get("requestedSchema"))
                else:
                    spec.update(url=web_url(params.get("url")),
                                elicitation_id=text(params.get("elicitationId"), 200))
            except (ValueError, KeyError, TypeError, OverflowError):
                self.reject(event, "MCP 表单或网页地址无效，或包含尚不支持的字段")
                return
            self.connect()
            if child is not None:
                self.loop.child_inputs.current(child.thread_id, turn_id=expected_turn)
            binding = {"connection_id": self.connection_id, "request_id": rpc_id,
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
            claim = self.audit.requests.check(rpc_id, expected_thread, [binding, spec])
            if claim is None:
                return
            current = self.audit.workbench.active()
            existing = [r for r in self.audit.index.audits.values()
                        if r["task_id"] == self.loop.state.task["id"]
                        and ("elicitation" in r or "input_origin" in r)]
            if len(existing) >= 32 or len(self.audit.pending) >= 4:
                self.reject(event, "本任务的 MCP 交互数量已达上限")
                self.audit.requests.remember(claim)
                return
            audit_id = "a_" + uuid.uuid4().hex
            title = server + " · " + ("填写表单" if mode == "form" else "网页交互")
            if child is not None:
                title = child.policy.name + " · " + title
            origin = {"thread_id": expected_thread, "turn_id": expected_turn,
                      "task_id": self.loop.state.task["id"],
                      "context": self.loop.state.context.record(), "request": binding}
            if child is not None:
                origin["child_scope"] = self.loop.child_inputs.scope(child)
            row = {"id": audit_id, "title": title,
                   "questions": [], "recommendation": "", "rationale": spec["message"],
                   "elicitation": spec, "context": self.loop.state.context.record(),
                   "task_id": self.loop.state.task["id"],
                   "work_id": current["work_id"], "stage_id": current["stage_id"],
                   "evidence": [{"id": current["source"],
                                 "digest": self.audit.index.data[current["source"]]["digest"]}]}
            self.emit("prepared", row, origin=origin)
            self.audit.pending[audit_id] = {"rpc_id": rpc_id, "binding": binding,
                                            "blocking": True, "source": "elicitation",
                                            "child_thread_id": child.thread_id if child else "",
                                            "child_turn_id": expected_turn if child else ""}
            self._sync_waiting()
            self.emit("opened", {"id": audit_id, **binding})
            self.audit.requests.remember(claim)
            self.loop._activity("waiting_user", "等待答复：" + row["title"])

    def current(self, audit_id, scope):
        self.audit.index.sync()
        row = self.audit.index.resolve("audit", audit_id)
        pending = self.audit.pending.get(audit_id)
        if (not pending or pending.get("source") != "elicitation"
                or scope != {"binding": row["binding"], "context": row["context"],
                             "task_id": row["task_id"]}
                or row["task_id"] != self.loop.state.task.get("id")
                or row["context"] != self.loop.state.context.record()
                or (not pending.get("child_thread_id")
                    and pending["binding"]["host_turn_id"] != self.loop.steering.turn_id)
                or (not pending.get("child_thread_id")
                    and pending["binding"]["thread_id"] != self.loop.thread_id)
                or self.loop.runtime is None or self.loop.runtime.rpc is not self.connection
                or self.connection_id != self.loop.connection_id
                or not self.loop.active or self.loop.cancelled() or self.loop.stale):
            raise ValueError("此交互已结束、来源已变化或不属于当前回合")
        if pending.get("child_thread_id"):
            self.loop.child_inputs.current(pending["child_thread_id"],
                                           turn_id=pending["child_turn_id"])
        return row

    def answer(self, audit_id, value, scope, reply_id, actor):
        with self.loop.lock:
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
        with self.loop.lock:
            row = self.current(audit_id, scope)
            if row["status"] != "pending" or row["elicitation"]["mode"] != "url":
                raise ValueError("此交互不再等待网页操作")
            return web_url(row["elicitation"]["url"])

    def deliver(self):
        delivered = 0
        # A resolution already queued by the reader wins over a local answer.
        poll = getattr(getattr(self.loop.runtime, "rpc", None), "poll_notifications", None)
        if self._pending() and callable(poll):
            poll()
        with self.loop.lock:
            for key, pending in self._pending():
                self.audit.index.sync()
                row = self.audit.index.audits[key]
                if row["status"] != "answer_received":
                    continue
                scope = {"binding": row["binding"], "context": row["context"],
                         "task_id": row["task_id"]}
                try:
                    self.current(key, scope)
                    self.audit.index.verify_evidence(row["evidence"])
                except (ValueError, OSError):
                    self.end("来源或证据已变化，答复未交付", keys=[key])
                    continue
                reply = deepcopy(row["reply"])
                # A durable marker precedes the one permitted send; recovery never replays it.
                try:
                    self.emit("dispatching", {"id": key, "reply_id": reply["reply_id"]})
                    try:
                        self.current(key, scope)
                    except ValueError:
                        self.end("交付前交互已结束或来源变化，答复未发送", keys=[key])
                        continue
                finally:
                    self.audit.pending.pop(key, None)
                    self._sync_waiting()
                try:
                    self.connection.send({"id": pending["rpc_id"], "result": reply["response"]})
                except (OSError, ValueError, RuntimeError):
                    self.emit("unconfirmed", {"id": key,
                              "reason": "答复发送结果未确认；不会自动重发"})
                    raise RpcError("MCP reply delivery is unconfirmed; no replay") from None
                self.emit("resumed", {"id": key, "reply_id": reply["reply_id"], "delivery": "sent"})
                self.loop._activity("waiting_model", "答复已发送，等待服务后续结果")
                delivered += 1
        return delivered

    def end(self, reason, *, keys=None, send=True):
        with self.loop.lock:
            failure = None
            for key, pending in self._pending():
                if keys is not None and key not in keys:
                    continue
                self.audit.pending.pop(key)
                self._sync_waiting()
                try:
                    self.emit("invalidated", {"id": key, "reason": reason})
                    if (send and self.loop.runtime and self.loop.runtime.rpc is self.connection
                            and pending["binding"]["connection_id"] == self.connection_id):
                        self.connection.send({"id": pending["rpc_id"],
                                              "result": {"action": "cancel"}})
                except (OSError, ValueError, RuntimeError) as exc:
                    failure = failure or exc
            if failure is not None:
                raise failure

    def resolved(self, event):
        return self.audit.requests.resolved(event)
