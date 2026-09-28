"""Qt-free owner of one session's queue, worker, cancellation and event snapshots."""

from __future__ import annotations

import threading
import uuid

from ..core.contracts import BoundContext
from ..core.startup import INITIAL_PROMPT, initial_context
from ..transport.targets import submission
from .backend_commands import SessionBackendCommands
from .binding_commands import SessionBindings
from .execution_view import ExecutionView
from .inbox import SessionInbox
from .published import freeze
from .router_commands import SessionRouter
from .reconcile_commands import SessionReconciliation
from .service_lifecycle import PendingWork
from .session_updates import SessionUpdates
from .task_history import TaskHistory
from .thread_commands import SessionThreadCommands


class SessionController:
    def __init__(self, loop):
        self.loop, self.journal = loop, loop.journal
        self.session_id = self.journal.session_id
        self.runtime_id = uuid.uuid4().hex
        self.label = getattr(loop.provider, "label", "开发 provider")
        self.base = getattr(loop.provider, "base", "")
        self.model = getattr(loop.provider, "model", "")
        self.current = BoundContext.from_record(loop.state.context.record())
        self.inbox = SessionInbox(self.journal, self.current)
        self.history = TaskHistory(self.journal, self.inbox)
        self._lock = threading.RLock()
        self._thread = None
        self._running = False
        self._cancel = threading.Event()
        self._shutdown = threading.Event()
        self._task = freeze(loop.state.task)
        self.paused = self._task.get("status") == "needs_reconcile"
        self.closing = False
        self.recovery = None
        self.fault = ""
        self.version = 0
        self._service_pending = PendingWork(sessions=1)
        # Replay earlier connections as history; new events must match this connection.
        self.history_sequence = len(self.journal.events())
        self.bindings = SessionBindings(
            self.journal, self.current, lock=self._lock, execution=self.execution_view,
            changed=self._changed, failed=self._failed, replace_current=self._replace_current,
            pending_contexts=lambda: [BoundContext.from_record(row["message"]["context"])
                                      for row in self.inbox.pending],
        )
        self.backend_commands = SessionBackendCommands(
            loop, lock=self._lock, execution=self.execution_view, check_input=self._check_input,
            binding_state=lambda: self.bindings.events.state,
            changed=self._changed,
        )
        self.thread_commands = SessionThreadCommands(
            loop, lock=self._lock, execution=self.execution_view, check_input=self._check_input,
            binding_state=lambda: self.bindings.events.state, interrupt=self.interrupt,
            changed=self._changed, launch=self._launch_operation,
        )
        self.reconciliation = SessionReconciliation(self)
        self.router = SessionRouter(
            self.journal, lock=self._lock, execution=self.execution_view,
            broker=lambda: self.bindings.broker, changed=self._changed,
        )
        self.updates = SessionUpdates(
            self.journal, lock=self._lock, execution=self.execution_view, bindings=self.bindings,
            history=self.history, steering_scope=self.backend_commands.steering_scope,
            steering_supported=getattr(loop, "steering", None) is not None,
            router_snapshot=self.router.snapshot,
        )
        # Legacy command aliases only, with no writable state forwarding.
        # Retire per owner when worker._command, startup, event_stream and quick
        # routing use component commands; inventory: tools/ai/docs/T07_STATE_OWNERSHIP.md.
        for component, names in (
            (self.bindings, ("reserve_submission", "release_submission", "attach_targets",
                "poll_binding_events", "propose_binding", "resolve_binding",
                "select_binding_target", "configure_bindings", "release_binding",
                "release_binding_resource", "reconcile_binding_operation", "released_targets")),
            (self.backend_commands, ("turn_input_status", "resource_status", "steer",
                "steering_scope", "check_target_request", "answer_audit", "elicitation")),
            (self.thread_commands, ("thread_scope", "thread_status", "thread_operation")),
            (self.reconciliation, ("query_interrupted", "resolve_interrupted")),
            (self.router, ("poll_router_status", "cancel_router_request", "router_receipt")),
            (self.updates, ("read_updates", "_state_snapshot")),
        ):
            for name in names:
                setattr(self, name, getattr(component, name))
        from .frontend_session import publish_session

        publish_session(self)

    def execution_view(self):
        return ExecutionView(
            self.session_id, self.runtime_id, self.current, self._running,
            len(self.inbox.pending), self.closing, self.fault, self._shutdown.is_set(),
            self._cancel.is_set(), self._task, self.paused, self.version,
        )

    def pending_work(self):
        """Published lifecycle observation; a busy I/O lock never blocks the listener.

        The live session count conservatively prevents idle exit while this last
        observation is stale. Detailed counters refresh only with an available lock.
        """
        if self._lock.acquire(blocking=False):
            try:
                self._service_pending = PendingWork(
                    sessions=1, queued=len(self.inbox.pending),
                    approvals=(len(self.bindings.events.pending_targets())
                               + len(self._task.get("waiting_audits", ()))),
                    jobs=int(self.busy) + len(getattr(self.loop, "_inflight", ())),
                    reconcile=int(self._task.get("status") == "needs_reconcile"
                                  or self.paused or bool(self.fault)))
            finally:
                self._lock.release()
        return self._service_pending

    def _changed(self):
        self.version += 1

    def _failed(self, message):
        self.fault = message

    def _replace_current(self, expected, replacement):
        if self.current == expected:
            self.current = replacement

    def initialize_context(self):
        with self._lock:
            if (self._task or self.inbox.pending or self.history.rows
                    or self.closing or self.fault or self._shutdown.is_set()
                    or self.bindings.resource_release_pending()):
                return False
            if not any(
                tool["name"] in {"get_context", "get_entry_context"}
                for tool in self.loop.tools.schemas()
            ):
                return False
            message = {
                "kind": "submit",
                "id": uuid.uuid4().hex,
                "text": INITIAL_PROMPT,
                "context": self.current.record(),
            }
            self.inbox.enqueue(message, self.current, "startup")
            self._accepted()
            return True

    @property
    def busy(self):
        return self._running

    def activity(self):
        """Navigation status for this session: active, pending, or idle."""
        if self._running:
            return "active"
        if self.paused or self.fault:
            return "pending"
        return "idle"

    @property
    def display_name(self):
        names = self.journal.name_metadata
        return names["name"] or names["title"] or self.session_id

    def poll_metadata(self):
        if not self._lock.acquire(blocking=False):
            return
        try:
            poll = getattr(self.loop, "poll_thread_names", None)
            if not self._running and not self.closing and callable(poll):
                try:
                    poll()
                except (OSError, ValueError, RuntimeError):
                    pass  # Optional metadata must not stop the background service.
            self.bindings.release_unused()
        finally:
            self._lock.release()

    def _check_input(self, text):
        if self.recovery is not None and self.recovery.blocked:
            raise ValueError("恢复的会话已暂停，请先核对原任务和输入，再明确恢复队列")
        self.bindings.check_submission()
        if self.bindings.sync_error:
            raise ValueError("捕获的 Virtuoso 来源不可用，请核对连接后再提交")
        if self.closing or self.fault or self._shutdown.is_set():
            raise ValueError("会话正在结束或需要重新打开")
        if not isinstance(text, str) or not text.strip() or "\0" in text:
            raise ValueError("请输入有效的任务内容")
        if len(text) > min(16000, self.loop.config.context_chars // 2):
            raise ValueError("输入超过本会话的长度限制")

    def submit(self, text, *, context=None, attachment_text=None, reservation=None,
               inputs=None, turn_options=None, input_id=None, service_address=None,
               submission_digest=None):
        with self._lock:
            self._check_input(text)
            default = (self.bindings.events.default_context
                       if self.bindings.events.default_explicit else self.current)
            context = context or default
            if context.target_id in (self.bindings.events.state or {}).get("invalidated", {}):
                raise ValueError("绑定窗口已关闭或切换设计，请选择仍有效的绑定目标")
            reserved = self.bindings.reserved(reservation)
            if (context not in (self.current, self.bindings.displayed,
                                self.bindings.events.default_context)
                    and reserved != context):
                raise ValueError("显示的来源已过期，请核对当前目标后重新提交")
            if len(self.inbox.pending) >= 16:
                raise ValueError("The session already has 16 waiting requests")
            controls = getattr(self.loop, "turn_options", None)
            if (inputs or turn_options is not None) and controls is None:
                raise ValueError("当前后端不支持多模态输入与回合设置")
            frozen = controls.freeze(turn_options) if controls is not None else None
            if controls is not None:
                controls.check_inputs(inputs or [], frozen)
            from ..storage.input_assets import prepare_inputs

            was_cancelled = self._cancel.is_set()
            prepared = prepare_inputs(self.journal, inputs or [],
                                      controls.catalog["skills"] if controls else ())
            attachments = []
            if attachment_text is not None:
                attachments.append(self.journal.text_attachment(attachment_text))
            request_id = input_id or uuid.uuid4().hex
            message = {
                "kind": "submit",
                "id": request_id,
                "text": text,
                "context": context.record(),
            }
            if attachments:
                message["attachments"] = attachments
            if prepared:
                message["inputs"] = prepared
            if frozen and frozen.get("model"):
                message["turn_options"] = frozen
            if submission_digest is not None:
                message["submission_digest"] = submission_digest
            # Out-of-band shutdown/cancel can occur while input files are being read.
            self._check_input(text)
            if self._cancel.is_set() and not was_cancelled:
                raise ValueError("输入读取期间任务已取消，草稿保留")
            if context.target_id in (self.bindings.events.state or {}).get("invalidated", {}):
                raise ValueError("输入来源已失效，草稿保留")
            if self.inbox.enqueue(message, context, service_address=service_address):
                self._accepted()
            return request_id

    def accept(self, message, *, service_address=None):
        context = submission(message)
        with self._lock:
            self._check_input(message["text"])
            controls = getattr(self.loop, "turn_options", None)
            frozen = controls.freeze() if controls is not None else None
            self.bindings.retain(context)
            try:
                accepted = self.inbox.accept(message, turn_options=frozen,
                                             service_address=service_address)
            except Exception:
                self.bindings.release_unused()
                raise
            if accepted:
                self._accepted()
            else:
                self.bindings.release_unused()
            return accepted

    def _accepted(self):
        self.history.record(self.inbox.pending[-1])
        if (not self._running and len(self.inbox.pending) == 1
                and self._task.get("status") in {"completed", "failed", "cancelled"}):
            self.paused = False
        self.version += 1
        self._start()

    def _start(self):
        if (
            self._running
            or self.bindings.resource_release_pending()
            or self.paused
            or self.closing
            or self._shutdown.is_set()
            or self.fault
            or self.bindings.sync_error
            or self.recovery is not None and self.recovery.blocked
            or self._task.get("status") == "needs_reconcile"
            or not self.inbox.pending
        ):
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, name="copilot-session-" + self.session_id)
        try:
            self._thread.start()
        except RuntimeError:
            self._running = False
            self.paused = True
            self.fault = "任务线程未能启动；已接收的输入已保存，请重新打开会话核对"
            self._thread = None

    def _run(self):
        try:
            while True:
                with self._lock:
                    self._cancel.clear()
                    if (self.paused or self.closing or self._shutdown.is_set()
                            or not self.inbox.pending):
                        return
                    self.bindings.events.refresh()
                    target = self.inbox.pending[0]["message"]["context"]["target_id"]
                    if target in (self.bindings.events.state or {}).get("invalidated", {}):
                        self.paused = True
                        self.version += 1
                        return
                    text, context, input_id = self.inbox.take()
                    attachments = self.inbox.active["message"].get("attachments", [])
                    extras = {key: self.inbox.active["message"][key]
                              for key in ("inputs", "turn_options")
                              if key in self.inbox.active["message"]}
                    if "turn_options" in self.inbox.active:
                        extras["turn_options"] = self.inbox.active["turn_options"]
                    self.current = context
                    self.bindings.select_task(context)
                    self.version += 1
                    self.bindings.release_unused()
                if self.inbox.active.get("origin") == "startup":
                    initialize = getattr(self.loop, "initialize_context", None)
                    options = {"context": context, "input_id": input_id}
                    stream = (
                        initialize(self._cancel.is_set, **options)
                        if initialize
                        else initial_context(self.loop, self._cancel.is_set, **options)
                    )
                else:
                    stream = self.loop.run(
                        text,
                        self._cancel.is_set,
                        context=context,
                        input_id=input_id,
                        attachments=attachments,
                        **extras,
                    )
                for event in stream:
                    with self._lock:
                        self.history.event(event)
                        self._task = freeze(self.loop.state.task)
                        self.version += 1
                with self._lock:
                    self.inbox.finish(self._task)
                    if self._task.get("status") in {"needs_reconcile", "failed"}:
                        self.paused = True
                    self.version += 1
        except Exception as exc:
            with self._lock:
                self.fault = "任务控制中断：" + type(exc).__name__ + "；请结束会话后重新打开"
                self.paused = True
                if self.inbox.active:
                    key = self.inbox.active["id"]
                    self.history.rows[key].update(status="needs_reconcile", diagnostic=self.fault)
        finally:
            with self._lock:
                self._running = False
                self.version += 1
                self._start()

    def cancel(self):
        with self._lock:
            self.paused = True
            self._cancel.set()
            self.version += 1

    def _launch_operation(self, controls, action, request_id):
        self._cancel.clear()
        self._running = True
        self._thread = threading.Thread(target=self._run_operation,
            args=(controls, action, request_id, self.current),
            name="copilot-operation-" + uuid.uuid4().hex)
        try:
            self._thread.start()
        except RuntimeError:
            self._running = False
            self.paused = True
            raise ValueError("操作线程未能启动；不会自动重试") from None
        self.version += 1

    def _run_operation(self, controls, action, request_id, context):
        try:
            for event in controls.run(action, self._cancel.is_set, context=context,
                                      input_id=request_id):
                with self._lock:
                    self.history.event(event)
                    self._task = freeze(self.loop.state.task)
                    self.version += 1
            with self._lock:
                if self._task.get("status") in {"needs_reconcile", "failed", "cancelled"}:
                    self.paused = True
        except Exception:
            with self._lock:
                self.fault = "线程操作中断；请重新打开会话核对，操作不会重发"
                self.paused = True
        finally:
            with self._lock:
                self._running = False
                self.version += 1
                self._start()

    def interrupt(self):
        """Out-of-band cancellation remains responsive while commands perform I/O."""
        self.paused = True
        self._cancel.set()

    def request_close(self):
        self._shutdown.set()
        self.interrupt()

    def resume(self):
        with self._lock:
            self._check_input("Resume queue")
            if (
                self._running
                or self.closing
                or self.fault
                or self._task.get("status") == "needs_reconcile"
            ):
                raise ValueError("请先核对并结束中断任务")
            self.paused = False
            self.version += 1
            self._start()

    def acknowledge_interrupted(self):
        with self._lock:
            if self._running:
                raise ValueError("请等待当前任务结束")
            event = self.loop.acknowledge_interrupted()
            self.history.event(event)
            self._task = freeze(self.loop.state.task)
            self.version += 1

    def close(self):
        with self._lock:
            if not self.closing:
                self.closing = True
                self._cancel.set()
                for record in self.inbox.pending:
                    self.history.rows[record["id"]]["status"] = "not_started"
                try:
                    self.inbox.close()
                except OSError:
                    self.fault = "未能保存队列结束状态；已接收的原始输入保留，不会自动重放"
                self.version += 1
            self.bindings.close()

    def rename(self, name):
        with self._lock:
            if self._running or self.closing:
                raise ValueError("会话正在运行任务，暂不能重命名")
            rename = getattr(self.loop, "set_thread_name", None)
            if not callable(rename):
                raise ValueError("当前会话后端不支持重命名")
            renamed = rename(name)
            self.version += 1
            return renamed

    def delete(self):
        with self._lock:
            if self._running or self.closing:
                raise ValueError("会话正在运行任务，暂不能删除")
            delete = getattr(self.loop, "delete_thread", None)
            if not callable(delete):
                raise ValueError("当前会话后端不支持删除会话")
            delete()
            self.close()

    def wait(self, timeout=None):
        thread = self._thread
        if thread:
            thread.join(timeout)
        if self.closing and not self.busy:
            close = getattr(self.loop, "close", None)
            if close:
                close()
        return not self.busy
