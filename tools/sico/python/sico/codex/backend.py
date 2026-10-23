"""Codex task/connection owner with compatibility instruction exports."""

from __future__ import annotations

import json
import threading
import time
import uuid

from ..core.contracts import (
    TERMINAL,
    BoundContext,
    RunConfig,
    RunState,
    ToolCall,
)
from ..core.startup import INITIAL_PROMPT
from ..core.backend_support import CODEX_SUPPORT
from ..core.target_validation import context_method
from ..orchestration import ChildTaskPolicy, CodexCollaborationClient, CopilotTaskOrchestrator
from ..orchestration.thread_binding import ThreadBinding
from ..storage.journal import BULK_KINDS
from .input_delivery import send_controls
from .backend_children import project_collaboration, project_subagent

# Compatibility alias; migrate instruction imports to backend_instructions.
from .backend_instructions import INSTRUCTIONS as INSTRUCTIONS
from .backend_notifications import route as route_notification
from .backend_source import (
    attachment_instruction,
    validate_target_result,
)
from .names import ThreadNames
from .native_history import NativeHistory
from .native_projection import NOTIFICATIONS as NATIVE_NOTIFICATIONS
from .native_projection import NativeProjection
from .resources import ExternalResources
from .runtime_lifecycle import (
    authorize_new_task_recovery,
    ensure_runtime,
    metadata_rpc,
    poll_runtime,
    recover_interrupted_task,
    retire_runtime,
    runtime_rpc,
)
from .settings import DEFAULT_IDLE_TIMEOUT, DEFAULT_TOOL_TIMEOUT
from .steering import TurnSteering
from .task_driver import TaskDriver
from .task_journal import TaskJournal
from .thread_startup import bind_thread
from .tool_dispatch import encode_receipt, execute_call
from .tool_receipts import current_receipt
from .turn_input import accept_input, idle_request
from .turn_loop import TurnLoop
from .wait_reads import WaitingReads

_KNOWN_NOTIFICATIONS = frozenset({
    "thread/name/updated", "item/started", "item/completed", "item/agentMessage/delta",
    "item/reasoning/textDelta", "item/reasoning/summaryTextDelta",
    "item/mcpToolCall/progress", "item/commandExecution/outputDelta",
    "item/fileChange/outputDelta", "item/permissions/outputDelta",
    "thread/tokenUsage/updated", "turn/completed", "error"}) | NATIVE_NOTIFICATIONS


class CodexBackend:
    support = CODEX_SUPPORT

    def __init__(self, provider, tools, journal, context, *, terminal=None):
        self.provider, self.tools, self.journal = provider, tools, journal
        self.terminal = terminal
        self.config = RunConfig()
        self.state = journal.state or RunState(context)
        records = journal.events(exclude=BULK_KINDS)
        bindings = [r["payload"] for r in records if r["kind"] == "codex.thread"]
        if not bindings and any(r["kind"] == "task.started" for r in records):
            raise ValueError("An existing Python session cannot be resumed as Codex")
        if self.state.context.record() != context.record():
            raise ValueError("Codex session belongs to another CAD target")
        self.lock = threading.RLock()
        self._thread_binding = ThreadBinding(bindings[-1] if bindings else None, lock=self.lock)
        self.orchestration = CopilotTaskOrchestrator(
            self._thread_binding, (r["payload"] for r in records if r["kind"] == "codex.child"))
        self.collaboration = CodexCollaborationClient(self.orchestration)
        self._child_events = set()
        self._child_status = {e["payload"]["thread_id"]: e["payload"] for e in records
                              if e["kind"] == "codex.child"}
        self.runtime, self.bridge = None, None
        self._pre_dispatch_recovery = False
        self._retired_runtime_recovery = False
        self._runtime_cleanup_unconfirmed = False
        from .input_assembly import request_owner, child_inputs

        self.inputs = request_owner(self)
        self.child_inputs = child_inputs(self)
        self._tools_changed = threading.Condition(self.lock)
        self._inflight = set()
        self._tool_stop = threading.Event()
        self.cancelled = lambda: True
        self._cancel_baseline = False
        self.active = False
        self.stale, self.stale_reason = False, ""
        self.tool_started_at = {}
        self.task_journal = TaskJournal(self.journal, self.state, self.lock, self._child_status, lambda: time)
        from .profile_setting import ProfileSetting

        # One automatic product envelope; explicit embedders may still select
        # a narrower scope before runtime assembly.
        self.tool_profile = ProfileSetting(self)
        from .feature_groups import FeatureSetting

        self.features = FeatureSetting(self)
        self.names = ThreadNames(self, records)
        self.native = NativeProjection(self, records)
        self.native_history = NativeHistory(self, records)
        self.resources = ExternalResources(self, records)
        from .turn_options import TurnOptions

        self.turn_options = TurnOptions(self, records)
        self.steering = TurnSteering(self, records)
        self.waiting_reads = WaitingReads(self)
        from .thread_operations import ThreadOperations

        self.thread_ops = ThreadOperations(self, records)
        self.steering.recover()
        recover_interrupted_task(self, records)

    @property
    def thread_id(self):
        return self._thread_binding.thread_id

    @property
    def history_mode(self):
        return self._thread_binding.history_mode

    def _bind_thread(self, action, thread, *, expected, roots=None):
        bind_thread(self._thread_binding, action, thread, expected=expected, roots=roots,
                    bound_roots=self.resources.bound, emit=self._event, access=self.turn_options.selected["access"])

    def _event(self, kind, payload):
        return self.task_journal.emit(kind, payload)

    def _finish(self, status, message=""):
        with self.lock:
            user_stopped = bool(self.cancelled()) and not self._cancel_baseline
            if hasattr(self, "audit"):
                self.audit.end_inputs("宿主任务已结束；答复不会转入下一任务", send=False)
            self.steering.end()
            self.active = False
            self._tool_stop.set()
            self.native.finish(unconfirmed=user_stopped)
            # A running task cannot hide unknown native receipts behind a terminal state.
            # Leaving an already paused task is the controller-authorized acknowledgement.
            if (not user_stopped
                    and self.state.task.get("status") != "needs_reconcile"
                    and "needs_reconcile" in self.native.completed.values()):
                status = "needs_reconcile"
                message += "；原生操作结果未确认，请核对原命令或文件现状，不会自动重放"
            elif user_stopped and "needs_reconcile" in self.native.completed.values():
                message += "；原生操作结果未确认；任务已停止，原操作不会自动重放"
            status, message = self.task_journal.seal_tools(
                status, message, self.tool_started_at, promote=not user_stopped)
            self.inputs.finish()
            self.state.task.update(status=status, diagnostic=message)
            self.state.task.pop("activity", None)
            return self._event("task." + status, {"message": message})

    def _activity(self, phase, label, *, tool=""):
        return self.task_journal.activity(phase, label, tool=tool)

    def _begin(self, text, context, input_id, origin, attachments=None,
               inputs=None, turn_options=None, *, cancel_baseline=None):
        if self._inflight:
            raise ValueError("上一任务的工具仍在收尾，请等待回执后继续")
        if self.state.task and self.state.task["status"] not in TERMINAL:
            raise ValueError("Task requires reconciliation")
        if (context.instance_id, context.generation) != (
            self.state.context.instance_id,
            self.state.context.generation,
        ):
            raise ValueError("Cannot change Virtuoso instance in a session")
        self.state.context = BoundContext.from_record(context.record())
        self._tool_stop = threading.Event()
        self.task_journal.begin(input_id, origin, attachments, inputs, turn_options)
        self.native.begin()
        self.task_journal.usage_turns = {}
        self.stale = False
        self.stale_reason = ""
        self._cancel_baseline = bool(self.cancelled()) if cancel_baseline is None else cancel_baseline
        return self.task_journal.started(
            text, context, input_id, origin, attachments, inputs, turn_options)

    @staticmethod
    def _attachment_instruction(attachments):
        return attachment_instruction(attachments)

    def initialize_context(self, cancelled, *, context, input_id):
        return self.run(
            INITIAL_PROMPT, cancelled, context=context, input_id=input_id, origin="startup"
        )

    def resume_thread(self):
        """Load an existing native Codex thread before accepting new input."""
        if not self.thread_id:
            raise ValueError("Cannot resume a Codex session without a native thread id")
        self._ensure_runtime()

    def _ensure_runtime(self):
        ensure_runtime(self)

    @property
    def connection_id(self):
        return self.inputs.connection_id

    def input_connection(self):
        """Bind every input type to the same connection owner."""
        return self.inputs.connect()

    def _input_request(self, event, turn_id, *, parent_ended=False):
        return accept_input(
            event, turn_id, parent_ended=parent_ended, thread_id=self.thread_id,
            children=self.child_inputs, audit=getattr(self, "audit", None),
            admitted=lambda: self.active and not self.cancelled() and not self.stale,
            rpc=self.runtime.rpc, emit=self._event)

    def _execute(self, name, arguments, *, internal=False):
        with self.lock:
            if not self.active or self.stale or self.cancelled():
                return {
                    "content": [{"type": "text", "text": "Task no longer active"}],
                    "isError": True,
                }
            context = BoundContext.from_record(self.state.context.record())
            blocked = None if internal else self.waiting_reads.check(name, arguments, context)
            if blocked is not None:
                return {
                    "content": [{"type": "text", "text": json.dumps(
                        blocked.record(), ensure_ascii=False)}],
                    "isError": True,
                }
            call = ToolCall(uuid.uuid4().hex, name, arguments)
            call.record()
            task = self.state.task
            stop, cancelled = self._tool_stop, self.cancelled
            self._inflight.add(call.id)
            self.state.task["pending_calls"].append(call.record())
            self.tool_started_at[call.id] = time.monotonic()
            self._activity("tool_call", "执行工具", tool=name)
            self._event("tool.started", call.record())
        try:
            return self._execute_call(call, context, task, stop, cancelled, internal=internal)
        finally:
            with self._tools_changed:
                self._inflight.discard(call.id)
                self._tools_changed.notify_all()

    def _execute_call(self, call, context, task, stop, cancelled, *, internal=False):
        result = execute_call(
            call, context, task, stop, cancelled, internal=internal,
            journal=self.task_journal, active=lambda: self.active,
            before_start=self.waiting_reads.before_start, execute=self.tools.execute)
        with self.lock:
            message, response = encode_receipt(
                call, result, self.config.tool_result_chars, self.journal.artifact)
            if not current_receipt(
                    call, result, context, task, message, journal=self.task_journal,
                    tool_started_at=self.tool_started_at, audit=getattr(self, "audit", None),
                    mark_stale=self._mark_stale):
                return response
            if not self.active or stop.is_set():
                pass  # Finish the receipt without announcing further model work.
            elif self.stale:
                self._activity("blocked", "任务已暂停：" + self.stale_reason)
            elif self.state.task.get("waiting_audits"):
                self._activity("waiting_user", "等待答复：" + self.audit.waiting_title())
            elif self.state.task["pending_calls"]:
                self._activity("processing_result", "整理工具结果")
            else:
                self._activity("waiting_model", "等待 LLM 响应")
        return response

    def _mark_stale(self, reason):
        self.stale = True
        self.stale_reason = self.stale_reason or reason

    def _drain(self):
        return self.task_journal.drain()

    def validate_audit_target(self):
        method = context_method(self.state.context, self.tools.schemas(), audit=True)
        result = self._execute(method, {}, internal=True)
        validate_target_result(result, self.journal.artifact_text)

    def _context_method(self, context):
        return context_method(context, self.tools.schemas())

    def run(
        self,
        text,
        cancelled=lambda: False,
        *,
        context=None,
        input_id=None,
        origin="chat",
        attachments=None,
        inputs=None,
        turn_options=None,
        _operation=None,
    ):
        context = context or self.state.context
        if turn_options is None:
            turn_options = self.turn_options.freeze()
        with self.lock:
            recover_stopped = authorize_new_task_recovery(self)
            self.cancelled = cancelled
            self._cancel_baseline = bool(cancelled())
            self._begin(text, context, input_id, origin, attachments, inputs, turn_options,
                        cancel_baseline=self._cancel_baseline)
            self.active = True
            self._pre_dispatch_recovery = recover_stopped
        driver = TaskDriver(
            self.task_journal, self.turn_options, self.thread_ops, self._thread_binding,
            connection=self._task_connection, source_status=lambda: (self.stale, self.stale_reason),
            execute=self._execute, schemas=self.tools.schemas, context_method=self._context_method,
            finish=self._finish, close=self.close, consume_turn=self._turn,
            attachment_instruction=self._attachment_instruction)
        try:
            yield from driver.drive(
                text, cancelled, context=context, input_id=input_id, origin=origin,
                attachments=attachments, inputs=inputs, turn_options=turn_options, operation=_operation)
        except Exception as exc:
            yield from driver.failed(exc, turn_options, _operation)
        finally:
            with self.lock:
                self.active = False
            if self.state.task["status"] == "executing":
                self.close()
                self._finish("needs_reconcile", "Codex task consumer disconnected; no replay")
            if _operation:
                self.thread_ops.finish()

    def _task_connection(self):
        recover_stopped = self._pre_dispatch_recovery
        self._pre_dispatch_recovery = False
        ensure_runtime(self, recover_stopped=recover_stopped)
        return self.runtime.rpc

    def _turn(self, turn_id, cancelled):
        while True:
            self.native_history.bind(turn_id)
            self.thread_ops.goals.bound(turn_id)
            self.steering.open(turn_id)
            try:
                continuing = yield from self._consume_turn(turn_id, cancelled)
            finally:
                try:
                    if hasattr(self, "audit"):
                        self.audit.elicitations.end("原回合已结束；此交互不会转入下一回合")
                finally:
                    self.steering.end(turn_id)
            if continuing != "goal_continue":
                return
            turn_id = self.thread_ops.goals.await_turn()
            self.state.task["turn"] = self.state.task.get("turn", 1) + 1

    def _consume_turn(self, turn_id, cancelled):
        return (yield from self._turn_loop().consume(
            turn_id, cancelled, idle_timeout=getattr(self.provider, "idle_timeout", DEFAULT_IDLE_TIMEOUT),
            tool_timeout=getattr(self.provider, "tool_timeout", DEFAULT_TOOL_TIMEOUT),
            max_output_chars=self.config.max_output_chars, known_notifications=_KNOWN_NOTIFICATIONS))

    def _turn_loop(self):
        return TurnLoop(
            self.runtime.rpc if self.runtime else None, self.thread_id,
            journal=self.task_journal, native=self.native, steering=self.steering,
            goals=self.thread_ops.goals, children=self.child_inputs,
            audit=getattr(self, "audit", None), names=self.names,
            **{key: getattr(self.runtime, key, None) for key in ("wrapper", "gateway")},
            tool_format=getattr(self.provider, "tool_format", "native"),
            source_status=lambda: (self.stale, self.stale_reason), close=self.close,
            finish=self._finish, resume=self._turn, notification=self._turn_notification,
            input_request=self._input_request, collaboration=self._record_collaboration,
            tool_started_at=self.tool_started_at, interrupt=self.thread_ops.interrupt,
            deactivate=self._deactivate, compact=(self.thread_ops.action or {}).get("kind") == "compact")

    def _deactivate(self):
        self.active = False

    def _turn_notification(self, event, turn_id):
        handled, drain = self._route_notification(event, turn_id=turn_id, consuming=True)
        if drain:
            yield from self._drain()
        return handled

    def _settle_turn(self, turn_id, status, cancelled):
        return (yield from self._turn_loop().settle(turn_id, status, cancelled))

    def _record_token_usage(self, params):
        self.task_journal.record_token_usage(params)

    def _record_collaboration(self, item):
        """Project Codex collaboration items into the Copilot session tree."""
        with self.lock:
            replies = project_collaboration(
                item, self.thread_id, self.collaboration, self.orchestration,
                self.child_inputs, self._child_events, self._child_scope, self._event)
        send_controls(replies)

    def expect_child_task(self, policy: ChildTaskPolicy) -> None:
        """Bind a business policy to the next native child spawn."""
        self.collaboration.expect_spawn(policy)

    def _child_scope(self, child):
        if (child.task_id and child.task_id == self.state.task.get("id")
                and child.connection_id == self.connection_id):
            return {"input_scope": self.child_inputs.scope(child)}
        return {}

    def _record_subagent(self, item, *, live=False):
        with self.lock:
            replies = project_subagent(
                item, live=live, thread_id=self.thread_id, statuses=self._child_status,
                orchestration=self.orchestration, child_inputs=self.child_inputs,
                child_scope=self._child_scope, emit=self._event)
        send_controls(replies)

    def _notification(self, event):
        return self._route_notification(event)[0]

    def _route_notification(self, event, *, turn_id=None, consuming=False):
        if event.get("method") == "skills/changed":
            self.resources.snapshot["inventory_stale"] = True
            self.resources.publish()
            self.turn_options.catalog["skills"] = []
            return True, consuming
        return route_notification(
            event, lock=self.lock, goals=self.thread_ops.goals, audit=getattr(self, "audit", None),
            names=self.names, children=self.child_inputs, history=self.native_history,
            active=self.active, turn_id=turn_id, consuming=consuming)

    def child_tasks(self):
        """Return child tasks observed for this Codex parent thread."""
        return tuple(self.orchestration.children.values())

    def acknowledge_interrupted(self):
        if self.state.task.get("status") != "needs_reconcile":
            raise ValueError("There is no interrupted task")
        self.close()
        with self.lock:
            if self._inflight:
                raise ValueError("工具仍在收尾，请等待回执后核对结果")
        self.state.task["pending_calls"] = []
        event = self._finish("cancelled", "Interrupted task reconciled without replay")
        list(self._drain())
        return event

    def set_thread_name(self, name):
        """Persist the user-facing name through Codex's native thread API."""
        self.names.validate(name)
        with self._metadata_rpc() as rpc:
            return self.names.set(rpc, name)

    def _metadata_rpc(self):
        return metadata_rpc(self)

    def _runtime_rpc(self):
        return runtime_rpc(self)

    def poll_thread_names(self):
        """Drain already received name updates without issuing an idle RPC."""
        poll_runtime(self)

    def _idle_request(self, event):
        return idle_request(event, active=self.active, audit=getattr(self, "audit", None),
                            rpc=self.runtime.rpc, emit=self._event)

    def delete_thread(self):
        """Persist native deletion acknowledgement before closing its connection."""
        with self._metadata_rpc() as rpc:
            result = rpc.request("thread/delete", {"threadId": self.thread_id})
            self._event("codex.thread.deleted", {"thread_id": self.thread_id})
        return result

    def close(self):
        try:
            with self.lock:
                self.active = False
                self._tool_stop.set()
                try:
                    if hasattr(self, "audit"):
                        self.audit.end_inputs("会话已关闭，未交付的答复不会重发", send=False)
                finally:
                    self.inputs.finish()
                    self.steering.end()
        finally:
            # Keep resource ownership until every close is confirmed.
            retire_runtime(self)

        with self._tools_changed:
            self._tools_changed.wait_for(lambda: not self._inflight, timeout=1.0)
