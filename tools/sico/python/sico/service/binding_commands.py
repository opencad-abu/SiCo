"""Binding commands and submission reservations; Qt-shared locks never enclose I/O."""

import uuid
import threading
from dataclasses import dataclass

from ..core.contracts import CircuitCallError, NeedsReconcile
from ..transport.bindings import binding_error
from ..transport.framing import ProtocolError
from .binding_events import SessionBindingEvents


@dataclass
class ResourceRelease:
    operation_id: str
    session_id: str
    runtime_id: str
    broker: object
    binding_identity: tuple
    revision: int
    reservation: dict
    unknown: bool = False


def binding_identity(state):
    return tuple(state.get(key) for key in ("binding_id", "instance_id", "generation"))


class SessionBindings:
    def __init__(self, journal, initial_context, *, lock, execution, changed,
                 failed, replace_current, pending_contexts):
        self._lock = lock
        self.execution = execution
        self.changed = changed
        self.failed = failed
        self.replace_current = replace_current
        self.pending_contexts = pending_contexts
        self.broker = None
        self.displayed = initial_context
        self.held = {}
        self._released = []
        self._submission_lock = threading.Lock()
        self._submissions = {}
        self._releasing_bindings = set()
        self._resource_release = None
        self.sync_error = ""
        self.events = SessionBindingEvents(
            journal, initial_context, read_source=self._event_source, changed=changed,
        )

    def _event_source(self):
        view = self.execution()
        return self.broker, view.session_id, view.current

    def publish_displayed(self):
        with self._submission_lock:
            self.displayed = (self.events.default_context if self.events.default_explicit
                              else self.execution().current)
            return self.displayed

    def reserved(self, key):
        with self._submission_lock:
            return self._submissions.get(key)

    def check_submission(self):
        with self._submission_lock:
            self._check_resource_release()

    def resource_release_pending(self):
        with self._submission_lock:
            return self._resource_release is not None

    def select_task(self, context):
        select = getattr(self.broker, "select_session_target", None)
        if callable(select) and not self.events.default_explicit:
            select(self.execution().session_id, context, actor="task")
            self.events.refresh()

    def retain(self, context):
        if self.broker:
            self.broker.register_target(context)
            self.held[context.target_id] = context
            register = getattr(self.broker, "register_session", None)
            if callable(register):
                try:
                    register(self.execution().session_id, context)
                except Exception:
                    self.release_unused()
                    raise
                self.events.register(context)

    def close(self):
        with self._submission_lock:
            self._resource_release = None
        release = getattr(self.broker, "release_session", None)
        if callable(release):
            release(self.execution().session_id)
            for target_id in list(self.held):
                self.broker.release_target(self.held.pop(target_id))
                self._released.append(target_id)

    def reserve_submission(self, context):
        key = uuid.uuid4().hex
        with self._submission_lock:
            if self.execution().closing or self.execution().fault or self.execution().shutdown:
                raise ValueError("会话正在结束或需要重新打开")
            self._check_resource_release()
            if context.target_id in self._releasing_bindings:
                raise ValueError("目标正在释放，请选择仍绑定的目标")
            if context not in (self.execution().current, self.displayed, self.events.default_context):
                raise ValueError("显示的来源已过期，请核对当前目标后重新提交")
            self._submissions[key] = context
            return key

    def release_submission(self, key):
        with self._submission_lock:
            self._submissions.pop(key, None)

    def attach_targets(self, broker):
        with self._lock:
            if self.execution().closing or self.execution().shutdown:
                raise ValueError("Session is closing")
            if self.broker is not None or self.execution().busy or self.execution().pending:
                raise RuntimeError("Target transport must be attached before submitting tasks")
            self.broker = broker
            # Session identity is registered independently from target
            # registration; both point at the same instance-level bridge.
            register = getattr(broker, "register_session", None)
            try:
                if callable(register):
                    register(self.execution().session_id, self.execution().current)
                self.events.refresh()
            except Exception:
                self.failed("会话目标注册失败，请重新打开 Silicon Copilot")
                self.changed()
                raise

    def poll_binding_events(self):
        if not self._lock.acquire(blocking=False):
            return
        try:
            if not self.execution().closing and not self.execution().shutdown:
                try:
                    operation = self._resource_release
                    expected = operation.binding_identity if operation else None
                    self.events.refresh(expected_binding=expected)
                    self._observe_resource_release()
                except Exception as error:
                    message = str(error)
                    if self.sync_error != message:
                        self.sync_error = message
                        self.changed()
                    return
                if self.sync_error and self._resource_release is None:
                    self.sync_error = ""
                    self.changed()
        finally:
            self._lock.release()

    def _check_binding_command(self):
        if self.execution().closing or self.execution().fault or self.execution().shutdown:
            raise ValueError("Session is closing or unavailable")
        if not callable(getattr(self.broker, "binding_snapshot", None)):
            raise ValueError("This connection does not support binding events")

    def _check_resource_release(self):
        """Called under the submission lock; only bounded in-memory checks belong here."""
        operation = self._resource_release
        if operation is not None:
            if operation.unknown:
                raise ValueError("资源释放结果待核对，请检查绑定证据或重新打开会话")
            raise ValueError("会话资源正在释放，请稍后重新提交；草稿已保留")

    def propose_binding(self, context, *, reason="", origin_request_id=""):
        """Host command with a captured context; not an Agent tool or raw window API."""
        with self._lock:
            self._check_binding_command()
            self.broker.register_target(context)
            self.held[context.target_id] = context
            try:
                return self.broker.propose_binding(self.execution().session_id, context, reason=reason,
                    task_id=self.execution().task.get("id", ""), origin_request_id=origin_request_id)
            finally:
                self.events.refresh()

    def resolve_binding(self, event_id, choice):
        with self._lock:
            self._check_binding_command()
            try:
                return self.broker.resolve_binding(self.execution().session_id, event_id, choice)
            finally:
                self.events.refresh()
                self.release_unused()

    def select_binding_target(self, target_id):
        with self._lock:
            self._check_binding_command()
            context = self.events.targets.get(target_id)
            if context is None:
                raise ValueError("Target is not bound to this session")
            self.broker.select_session_target(self.execution().session_id, context)
            self.events.refresh()

    def configure_bindings(self, *, auto_bind, timeout_seconds):
        with self._lock:
            self._check_binding_command()
            self.broker.configure_bindings(self.execution().session_id, auto_bind=auto_bind,
                                            timeout_seconds=timeout_seconds)
            self.events.refresh()

    def release_binding(self, target_id):
        with self._lock:
            self._check_binding_command()
            context = self.events.targets.get(target_id)
            if context is None:
                raise ValueError("Target is not bound to this session")
            with self._submission_lock:
                self._check_resource_release()
                reserved = bool(self._submissions)
                busy = self.execution().busy or self.execution().pending or reserved
                if not busy:
                    self._releasing_bindings.add(target_id)
            if busy:
                raise binding_error("binding_busy", "Session has active or queued tasks", context)
            try:
                self.broker.release_binding(self.execution().session_id, context)
                self.events.refresh()
                if self.execution().current == context:
                    self.replace_current(context, self.events.default_context)
                if self.displayed == context:
                    self.displayed = self.events.default_context
                self.broker.release_target(context)
                self.held.pop(target_id, None)
                self._released.append(target_id)
            finally:
                with self._submission_lock:
                    self._releasing_bindings.discard(target_id)

    def release_binding_resource(self, reservation_id):
        with self._lock:
            self._check_binding_command()
            state = self.events.state or {}
            reservation = next((row for row in state.get("reservations", [])
                                if row["reservation_id"] == reservation_id), None)
            if reservation is None or not all(binding_identity(state)):
                raise ValueError("Resource reservation is not owned by this session")
            operation = ResourceRelease(
                uuid.uuid4().hex, self.execution().session_id, self.execution().runtime_id, self.broker,
                binding_identity(state), state["revision"], reservation,
            )
            with self._submission_lock:
                self._check_resource_release()
                if self.execution().busy or self.execution().pending or self._submissions:
                    raise ValueError("Session has active or queued tasks")
                # Resources can be used by any subsequent task in this session.
                # Serialise release with all submissions, without waiting for RPC on Qt.
                self._resource_release = operation
            self.changed()
        try:
            result = operation.broker.release_binding_resource(operation.session_id, reservation_id)
        except Exception as error:
            # A malformed response cannot prove that the mutation was refused.
            definite = (isinstance(error, ValueError) and not isinstance(error, ProtocolError)) or (
                isinstance(error, CircuitCallError)
                and error.data.get("operation_dispatched") is False
            )
            self._finish_resource_release(operation, unknown=not definite)
            raise
        try:
            with self._lock:
                self._require_resource_release(operation)
                if (binding_identity(result) != operation.binding_identity
                        or result["revision"] <= operation.revision
                        or any(row["reservation_id"] == reservation_id
                               for row in result["reservations"])):
                    raise NeedsReconcile("Resource release receipt does not match its binding")
                # The bridge receipt alone is not a local committed event. Mirror it
                # before unblocking submissions; a failed fsync remains pending.
                self.events.refresh(expected_binding=operation.binding_identity)
                self._require_resource_release(operation)
                if not self._resource_release_observed(operation):
                    raise NeedsReconcile("Resource release awaits committed binding evidence")
                self._finish_resource_release(operation, unknown=False)
        except Exception:
            self._finish_resource_release(operation, unknown=True)
            raise

    def _require_resource_release(self, operation):
        if (self._resource_release is not operation
                or self.execution().session_id != operation.session_id
                or self.execution().runtime_id != operation.runtime_id
                or self.broker is not operation.broker or self.execution().closing or self.execution().shutdown
                or binding_identity(self.events.state or {}) != operation.binding_identity):
            raise ValueError("Resource release completed for a previous session binding")

    def _finish_resource_release(self, operation, *, unknown):
        with self._lock:
            with self._submission_lock:
                if self._resource_release is not operation:
                    return  # A late completion cannot clear another operation's marker.
                if (self.execution().session_id != operation.session_id
                        or self.execution().runtime_id != operation.runtime_id
                        or self.broker is not operation.broker or self.execution().closing
                        or self.execution().shutdown):
                    self._resource_release = None
                    return
                if unknown:
                    operation.unknown = True
                else:
                    self._resource_release = None
            self.sync_error = (
                "资源释放结果待核对，请检查绑定证据或重新打开会话" if unknown else ""
            )
            self.changed()

    def _resource_release_observed(self, operation):
        state = self.events.state
        return (state["revision"] > operation.revision
                and not any(row["reservation_id"] == operation.reservation["reservation_id"]
                            for row in state["reservations"]))

    def _observe_resource_release(self):
        operation = self._resource_release
        if operation is None or not operation.unknown:
            return
        self._require_resource_release(operation)
        # Read-only reconciliation: a successful snapshot with the same binding
        # lifetime and a newer revision proves removal, without replaying the RPC.
        if self._resource_release_observed(operation):
            self._finish_resource_release(operation, unknown=False)

    def reconcile_binding_operation(self, binding_id, request_id):
        with self._lock:
            self._check_binding_command()
            broker, runtime_id = self.broker, self.execution().runtime_id
        result = broker.reconcile_binding_operation(self.execution().session_id, binding_id, request_id)
        with self._lock:
            if broker is not self.broker or runtime_id != self.execution().runtime_id or self.execution().closing:
                raise ValueError("Reconciliation completed for a previous session view")
            self.events.refresh()
        return result

    def release_unused(self):
        if not self.broker:
            return
        retained = {self.execution().current.target_id, self.displayed.target_id}
        retained.update(self.events.targets)
        retained.update(self.events.pending_targets())
        with self._submission_lock:
            retained.update(context.target_id for context in self._submissions.values())
        retained.update(context.target_id for context in self.pending_contexts())
        for key in list(self.held):
            if key not in retained:
                self.broker.release_target(self.held.pop(key))
                self._released.append(key)

    def released_targets(self):
        if not self._lock.acquire(blocking=False):
            return []
        try:
            released, self._released = self._released, []
            return released
        finally:
            self._lock.release()
