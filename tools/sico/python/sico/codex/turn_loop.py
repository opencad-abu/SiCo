"""Consume one native turn and settle its audit/child continuations."""

import logging
import queue
import threading

from ..core.contracts import NeedsReconcile
from .rpc import RpcError
from .turn_requests import respond
from .turn_response import EMPTY_RESPONSE, TurnResponse
from .turn_error import failure_message


class TurnLoop:
    def __init__(self, rpc, thread_id, *, journal, native, steering, goals,
                 children, audit, names, source_status, close, finish, resume,
                 notification, input_request, collaboration, tool_started_at,
                 interrupt, deactivate, compact=False, wrapper=None, gateway=None, tool_format="native"):
        self.interrupt, self.deactivate = interrupt, deactivate
        self.rpc, self.thread_id = rpc, thread_id
        self.journal, self.native, self.steering = journal, native, steering
        self.goals, self.children, self.audit, self.names = goals, children, audit, names
        self.source_status = source_status
        self.close, self.finish, self.resume = close, finish, resume
        self.notification, self.input_request = notification, input_request
        self.collaboration, self.tool_started_at = collaboration, tool_started_at
        self.response = TurnResponse(compact=compact)
        self.wrapper, self.tool_format = wrapper, tool_format
        self.gateway = gateway
        self.failure = None

    def consume(self, turn_id, cancelled, *, idle_timeout, tool_timeout,
                max_output_chars, known_notifications):
        interrupted = False
        parent_result = None
        deadline = None
        messages = {}
        completed = set()
        self.journal.progress()
        while True:
            if parent_result is not None and not self.children.active():
                return (yield from self.settle(turn_id, parent_result, cancelled))
            ended, interrupted, deadline = yield from self.tick(
                turn_id, cancelled, interrupted, deadline, parent_result, idle_timeout, tool_timeout)
            if ended == "repeat":
                continue
            if ended:
                return
            try:
                event = self.rpc.next()
            except queue.Empty:
                continue
            method, params = event.get("method"), event.get("params", {})
            if (yield from self.notification(event, turn_id)):
                continue
            if "id" in event and method:
                respond(event, method, params, turn_id, parent_result, thread_id=self.thread_id,
                        input_request=self.input_request, send=self.rpc.send, progress=self.journal.progress)
                if self.audit is not None and self.audit.pending:
                    self.response.interaction()
                continue
            if method not in known_notifications:
                # App-server may add notifications without making them part of
                # this task contract. Ignore them before interpreting params.
                continue
            if params.get("threadId") != self.thread_id or params.get("turnId", turn_id) != turn_id:
                continue
            self.journal.progress()
            if method == "error":
                if params.get("willRetry") is not True:
                    self.failure = failure_message(
                        params.get("error"), wrapper=self.wrapper, gateway=self.gateway, tool_format=self.tool_format)
                continue
            self.response.observe(method, params)
            if method == "thread/tokenUsage/updated":
                self.journal.record_token_usage(params)
                yield from self.journal.drain()
                continue
            if self.native.receive(method, params, turn_id):
                yield from self.journal.drain()
                continue
            if method == "item/agentMessage/delta":
                key, delta = params["itemId"], params["delta"]
                if key in completed:
                    raise RpcError("Codex streamed text after message completion")
                messages[key] = messages.get(key, "") + delta
                if sum(map(len, messages.values())) > max_output_chars:
                    raise RpcError("Codex output exceeds Copilot display budget")
                self.journal.append_delta(delta)
                yield from self.journal.drain()
            elif method == "item/completed" and params["item"]["type"] == "agentMessage":
                item = params["item"]
                key, final = item["id"], item.get("text", "")
                if key in completed:
                    continue
                previous = messages.get(key, "")
                if not final.startswith(previous):
                    raise RpcError("Codex final text conflicts with the streamed message")
                suffix = final[len(previous) :]
                messages[key] = final
                if sum(map(len, messages.values())) > max_output_chars:
                    raise RpcError("Codex output exceeds Copilot display budget")
                self.journal.append_completed(previous, suffix, final)
                completed.add(key)
                yield from self.journal.drain()
            elif (method in {"item/started", "item/completed"}
                  and params["item"].get("type") == "collabAgentToolCall"):
                self.collaboration(params["item"])
                yield from self.journal.drain()
            elif method == "turn/completed" and params["turn"]["id"] == turn_id:
                status = self.native_turn_ended(turn_id, params, cancelled)
                if status == "completed" and self.children.active():
                    parent_result = status
                    continue
                return (yield from self.settle(turn_id, status, cancelled))

    def close_connection(self):
        self.close()
        self.rpc = None

    def tick(self, turn_id, cancelled, interrupted, deadline, parent_result,
                   idle_timeout, tool_timeout):
        now = self.journal.clock().monotonic()
        self.native.flush()
        if interrupted and now >= deadline:
            raise RpcError("中断请求未及时结束，已关闭连接；请核对原操作结果，不会自动重跑。")
        with self.journal.lock:
            waiting = (self.audit is not None and (
                self.audit.waiting_for_input() or self.audit.waiting_for_host()
                or any(p.get("child_thread_id") for p in self.audit.pending.values())
            ))
            native_started = [row["started_at"] for row in self.native.pending.values()
                              if row["native"]["item_type"] != "plan"]
            running = bool(self.journal.state.task["pending_calls"] or native_started)
            if waiting or running:
                self.journal.last_progress = now
            oldest = min([*self.tool_started_at.values(), *native_started], default=now)
            if not interrupted and not cancelled() and not self.source_status()[0]:
                if running and now - oldest > tool_timeout:
                    raise RpcError("工具执行超过配置时限；请核对原操作结果，不会自动重跑。")
                if not waiting and not running and now - self.journal.last_progress > idle_timeout:
                    raise RpcError("模型持续无响应，已停止等待；请检查连接并核对原操作结果，"
                                   "不会自动重跑。")
        yield from self.journal.drain()
        # Let the active turn consume a requestUserInput item before
        # applying a host answer.  In Codex 0.154 the model can emit that
        # item after the host question is answered; interrupting here
        # would make the app-server abort the item and the next turn
        # would receive the old response instead of the user's answer.
        if (cancelled() or self.source_status()[0]) and not interrupted:
            self.goals.pause(self.rpc, "cancelled")
            if self.audit is not None:
                self.audit.end_inputs("任务已取消或来源失效，交互已结束")
            self.steering.end(turn_id)
            if parent_result is not None:
                self.close_connection()
                self.finish("needs_reconcile" if self.source_status()[0] else "cancelled",
                             self.source_status()[1] or "Cancelled while child input was pending")
                yield from self.journal.drain()
                return True, interrupted, deadline
            self.interrupt(turn_id)
            interrupted = True
            deadline = self.journal.clock().monotonic() + 15
        if not interrupted and self.audit is not None:
            if self.audit.pending:
                self.goals.pause(self.rpc, "waiting_user")
            self.audit.deliver()
            if self.source_status()[0] or cancelled():
                return "repeat", interrupted, deadline
        if not interrupted:
            if self.children.active():
                self.goals.pause(self.rpc, "child_active")
            self.steering.dispatch()
        return False, interrupted, deadline

    def native_turn_ended(self, turn_id, params, cancelled):
        if self.children.active() or (self.audit is not None and self.audit.pending):
            self.response.interaction()
        if self.audit is not None:
            keys = [key for key, p in self.audit.pending.items()
                    if not p.get("child_thread_id")]
            self.audit.elicitations.end("原回合已结束；未交付的答复不会重发", keys=keys)
        self.steering.end(turn_id)
        status = params["turn"]["status"]
        if status == "failed" and (params["turn"].get("error") or not self.failure):
            self.failure = failure_message(
                params["turn"].get("error"), wrapper=self.wrapper, gateway=self.gateway, tool_format=self.tool_format)
        if status != "completed" or cancelled():
            logging.getLogger(__name__).warning(
                "Native turn ended: session=%s task=%s turn=%s status=%s "
                "local_cancel=%s pending_tools=%s",
                self.journal.journal.session_id, self.journal.state.task["id"], turn_id,
                status, cancelled(), len(self.journal.state.task["pending_calls"]),
            )
        self.native.finish(turn_id)
        return status

    def settle(self, turn_id, status, cancelled):
        if self.goals.running and (cancelled() or self.source_status()[0] or status != "completed"):
            self.goals.pause(self.rpc, "turn_stopped")
        if self.audit is not None:
            keys = [key for key, p in self.audit.pending.items() if p.get("child_thread_id")]
            self.audit.end_inputs("子任务来源已结束；答复不会转入下一任务", keys=keys)
        if status == "completed" and not cancelled() and not self.source_status()[0]:
            if self.goals.running and self.journal.state.task.get("waiting_audits"):
                self.goals.pause(self.rpc, "waiting_user")
            if self.audit is not None and self.audit.host_answer_ready():
                yield from self.continue_after_host(cancelled)
                return
            if self.journal.state.task.get("waiting_audits"):
                yield from self.wait_for_audit_answer(turn_id, cancelled)
                return
            if not self.response.usable:
                self.goals.pause(self.rpc, "empty_response")
                self.close_connection()
                self.finish("needs_reconcile", EMPTY_RESPONSE)
                yield from self.journal.drain()
                return
            if self.goals.continuing():
                return "goal_continue"
        with self.journal.lock:
            self.deactivate()
            pending = bool(self.journal.state.task["pending_calls"])
        if status != "completed" or pending or self.source_status()[0] or cancelled():
            self.close_connection()
        if self.source_status()[0]:
            self.finish("needs_reconcile", self.source_status()[1])
        elif status == "interrupted" or cancelled():
            self.finish("cancelled", "Generation stopped; no external job was stopped")
        elif status == "completed":
            if self.rpc is not None:
                self.names.after_turn(self.rpc)
            self.finish("completed")
        else:
            self.finish("failed", self.failure or failure_message(
                wrapper=self.wrapper, gateway=self.gateway, tool_format=self.tool_format))
        yield from self.journal.drain()

    def wait_for_audit_answer(self, turn_id, cancelled):
        """Keep an ended model turn alive while Copilot audits await answers."""
        while self.audit is not None and self.audit.pending:
            if cancelled() or self.source_status()[0]:
                if cancelled() and not self.source_status()[0]:
                    self.finish("cancelled", "Task cancelled while waiting for the user's answer")
                    yield from self.journal.drain()
                    return
                raise NeedsReconcile(self.source_status()[1])
            replies = self.audit.deliver_host()
            if self.source_status()[0]:
                raise NeedsReconcile(self.source_status()[1])
            if replies:
                # Host prerequisites are safe to continue immediately. Native
                # blocking requests still need their RPC answer first.
                if self.audit.waiting_for_input():
                    self.journal.activity("waiting_user", "等待答复：" + self.audit.pending_title())
                    yield from self.journal.drain()
                    threading.Event().wait(0.1)
                    continue
                content = "\n".join(reply["content"] for reply in replies)
                result = self.rpc.request(
                    "turn/start",
                    {"threadId": self.thread_id, "input": [{"type": "text", "text": content}]},
                )
                yield from self.resume(result["turn"]["id"], cancelled)
                return
            # Deliver native answers as soon as the host has submitted them.
            delivered = self.audit.deliver()
            if not self.audit.pending:
                # A blocking native request should normally keep the Codex
                # turn open. If an app-server completes early, continue
                # consuming that same turn after sending its answer instead
                # of leaving the task in an unresumable executing state.
                if delivered:
                    yield from self.resume(turn_id, cancelled)
                return
            self.journal.activity("waiting_user", "等待答复：" + self.audit.pending_title())
            yield from self.journal.drain()
            threading.Event().wait(0.1)

    def continue_after_host(self, cancelled):
        """Keep a host-opened question visible, then continue the same thread."""
        while self.audit is not None and self.audit.waiting_for_host():
            if cancelled() or self.source_status()[0]:
                if cancelled() and not self.source_status()[0]:
                    self.close_connection()
                    self.finish("cancelled", "Task cancelled while waiting for the user's answer")
                    yield from self.journal.drain()
                    return
                if self.source_status()[0]:
                    raise NeedsReconcile(self.source_status()[1])
                return
            replies = self.audit.deliver_host()
            if self.source_status()[0]:
                raise NeedsReconcile(self.source_status()[1])
            if not replies:
                self.journal.activity("waiting_user", "等待答复：" + self.audit.pending_title())
                yield from self.journal.drain()
                threading.Event().wait(0.1)
                continue
            content = "\n".join(reply["content"] for reply in replies)
            result = self.rpc.request(
                "turn/start",
                {
                    "threadId": self.thread_id,
                    "input": [{"type": "text", "text": content}],
                },
            )
            yield from self.resume(result["turn"]["id"], cancelled)
            return
