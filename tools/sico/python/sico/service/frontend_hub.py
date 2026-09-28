"""Bounded service frontend dispatch; project/session ownership stays authoritative."""

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack

from ..core.contracts import BoundContext
from ..transport.framing import ProtocolError
from .cleanup_task import DEFAULT_CLEANUP_SECONDS, CleanupTask
from .frontend_attach import FrontendAttach, session_record
from .frontend_replay import FrontendReplay
from .project_workspace import ProjectWorkspace
from .service_inputs import ServiceInputs
from .service_protocol import exact_fields
from .service_session_dto import SessionAddress
from .session_control import SessionControl
from .session_end import SessionEnd
from .session_recovery import SessionRecovery


class FrontendHub:
    def __init__(self, owner, descriptor, control=None):
        self.owner, self.descriptor = owner, descriptor
        self.control = control or SessionControl(owner, descriptor)
        self.recovery = SessionRecovery(owner, descriptor)
        self.ending = SessionEnd(owner, self.control, retired=self.recovery.retired)
        with ExitStack() as pending:
            pending.callback(self.recovery.close)
            self.workspace = ProjectWorkspace(owner)
            pending.callback(self.workspace.close)
            self.attach = FrontendAttach(owner, descriptor)
            pending.callback(self.attach.close)
            self.replay = FrontendReplay(self.workspace, descriptor)
            self.inputs = ServiceInputs(owner, descriptor, self.control)
            self.pools = {}
            for kind in ("attach", "events", "query", "command"):
                pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="copilot-" + kind)
                pending.callback(pool.shutdown)
                self.pools[kind] = pool
            pending.pop_all()
        # Queries cannot occupy event or admission capacity. Transport/heartbeats
        # continue on the listener even while these bounded adapters are waiting.
        self.slots = {kind: threading.BoundedSemaphore(8) for kind in self.pools}
        self.closed = threading.Event()
        self._lock = threading.Lock()
        self._active = set()
        self._cleanup = CleanupTask(self._release, "copilot-frontend-close")

    @property
    def pending(self):
        with self._lock:
            return len(self._active)

    def start(self, wire, payload):
        kind = wire.method.split(".")[1]
        entry = wire, object()
        with self._lock:
            if self.closed.is_set() or not self.slots[kind].acquire(blocking=False):
                raise ProtocolError("Frontend server capacity unavailable")
            self._active.add(entry)
            try:
                self.pools[kind].submit(self._execute, entry, payload, kind)
            except BaseException:
                self._active.remove(entry)
                self.slots[kind].release()
                raise

    def _execute(self, entry, payload, kind):
        try:
            entry[0].execute(payload)
        finally:
            with self._lock:
                self._active.discard(entry)
            self.slots[kind].release()

    def execute(self, wire, row):
        exact_fields(row, {"operation", "params"})
        operation, params = row["operation"], row["params"]
        if not isinstance(operation, str) or not isinstance(params, dict):
            raise ProtocolError("Invalid frontend request")
        if wire.method == "frontend.attach":
            return self.attach.execute(wire, operation, params)
        if wire.method == "frontend.events":
            return self.replay.events(wire, operation, params)
        if wire.method == "frontend.query":
            if operation == "home":
                exact_fields(params, set())
                return {"platforms": ["CDNS-IC"]}
            if operation == "platform_list":
                from ..transport.host_discovery import list_instances

                exact_fields(params, {"platform"})
                if params["platform"] != "CDNS-IC":
                    raise ValueError("此 IC 平台暂未支持")
                return list_instances(self.owner.project, cancelled=wire.closed.is_set)
            if operation == "deletion_review":
                from .record_deletion import deletion_review

                exact_fields(params, {"session_id"})
                return deletion_review(self, params["session_id"])
            if operation == "recovery":
                exact_fields(params, {"session_id", "operation_id"})
                return self.recovery.inspect(params["session_id"], params["operation_id"])
            if operation == "catalog":
                exact_fields(params, {"version"})
                return dict(catalog=self.workspace.catalog.snapshot(params["version"]),
                    sessions=[session_record(c, self.descriptor)
                              for c in self.owner.controllers.values()])
            if operation in {"tool_result", "background"}:
                exact_fields(params, {"address", "args", "kwargs"})
                address, controller = self.controller(params["address"])
                args, kwargs = params["args"], params["kwargs"]
                if operation == "background" and (not args or args[0] not in {
                        "list_background", "get_background_status", "read_background_result"}):
                    raise ValueError("后台任务修改需要会话控制权")
                result = self.workspace.worker.session_query(address.session, operation,
                                                              *args, **kwargs).result()
                self.controller(params["address"])
                return result
            return self.replay.query(wire, operation, params)
        return self.command(wire, operation, params)

    def controller(self, row):
        address = SessionAddress.from_record(row)
        if (address.project_id, address.service_id) != (self.descriptor.project_id,
                                                       self.descriptor.service_id):
            raise ProtocolError("Frontend session belongs to another service")
        controller = self.owner.controllers.get(address.session.session_id)
        if controller is None or controller.runtime_id != address.session.runtime_id:
            raise ValueError("会话运行实例已变化，请重新连接")
        return address, controller

    def command(self, wire, operation, params):
        if operation == "continue_session":
            from .session_continuation import continue_session

            return continue_session(self, wire, params)
        if operation == "recover_session":
            return self.recovery.restore(wire.connection, wire.operation, params)
        if operation == "control":
            return self.control.execute(wire.connection, params).record()
        if operation in {"end_session", "end_status"}:
            exact_fields(params, {"address", "operation_id"})
            address = SessionAddress.from_record(params["address"])
            from .service_values import name

            name(params["operation_id"])
            if address.project_id != self.descriptor.project_id:
                raise ProtocolError("Session end belongs to another service")
            if operation == "end_status":
                return self.ending.observe(address, params["operation_id"]).record()
            if address.service_id != self.descriptor.service_id:
                raise ValueError("原服务已结束，请核对原请求")
            if params["operation_id"] != wire.operation:
                raise ProtocolError("Session end operation identity changed")
            return self.ending.request(address, wire.operation,
                                       wire.connection, wire.control).record()
        if operation == "record_command":
            from .session_records import record_command

            return record_command(self, wire, params)
        if operation == "create":
            exact_fields(params, {"address", "args", "kwargs"})
            address = SessionAddress.from_record(params["address"])
            if ((address.project_id, address.service_id) !=
                    (self.descriptor.project_id, self.descriptor.service_id)):
                raise ProtocolError("Creation source belongs to another service")
            args = params["args"]
            if (not isinstance(args, (list, tuple)) or len(args) != 1
                    or params["kwargs"] or not isinstance(args[0], BoundContext)):
                raise ProtocolError("Invalid session creation")
            return self._create(wire, address, args[0])
        exact_fields(params, {"address", "args", "kwargs"})
        address, controller = self.controller(params["address"])
        args, kwargs = params["args"], params["kwargs"]
        if not isinstance(args, (tuple, list)) or not isinstance(kwargs, dict):
            raise ProtocolError("Invalid frontend command arguments")
        if operation in {'submit_background', 'cancel_background'}:
            from .background_commands import background_command

            return background_command(self.workspace, self.control, address, wire,
                                      operation, args, kwargs)
        from .service_session_commands import COMMANDS, session_command

        if operation in COMMANDS:
            return session_command(self.workspace, self.control, address, wire,
                                   operation, args, kwargs)
        if operation == "submit":
            allowed = {"context", "attachment_text", "inputs", "turn_options"}
            if len(args) != 1 or set(kwargs) - allowed:
                raise ProtocolError("Invalid frontend input")
            context = kwargs.get("context") or controller.frontend_view.context
            if not isinstance(context, BoundContext):
                raise ProtocolError("Invalid frontend target")
            result = self.inputs.controlled(address, wire.operation, args[0], context.record(),
                                            wire.connection, wire.control,
                                            extras={key: value for key, value in kwargs.items()
                                                    if key != "context" and value is not None})
            if result.outcome == "rejected":
                raise ValueError("任务未接收：" + result.code)
            if result.outcome != "accepted":
                raise RuntimeError("Input outcome unknown")
            return result.input_id
        if operation == "attach_targets":
            if args or kwargs:
                raise ProtocolError("Target registration has no frontend arguments")
            return None  # Registered exactly once by ProjectSessionOwner.
        if operation == "query_interrupted":
            if len(args) != 1 or set(kwargs) != {"expected_task"}:
                raise ProtocolError("Query requires the original task and call identity")
            return self.workspace.worker.command(address.session, operation, *args, **kwargs).result()
        if operation in {"resource_status", "turn_input_status", "thread_status"}:
            if args or kwargs:
                raise ProtocolError("Unexpected command arguments")
            return self.workspace.worker.command(address.session, operation).result()
        if operation == "initialize_context":
            if args or kwargs:
                raise ProtocolError("Unexpected initialization arguments")
            return self.control.run(address, wire.connection, wire.control,
                lambda: self.workspace.worker.command(address.session, operation).result())
        if operation == "rename":
            if len(args) != 1 or kwargs:
                raise ProtocolError("Invalid session name")
            result = self.control.run(address, wire.connection, wire.control,
                lambda: self.workspace.worker.command(address.session, "rename", args[0]).result())
            self.workspace.catalog.request().result()
            return result
        self.control.run(address, wire.connection, wire.control, lambda: None)
        raise ValueError("此操作尚未开放；原任务继续运行，输入已保留")

    def _create(self, wire, address, context):
        # Preserve the source runtime while capturing dependencies for the new session.
        with self.owner._mutation:
            if address.session.session_id in self.owner.controllers:
                new = self.control.run(address, wire.connection, wire.control,
                    lambda: self.owner.create_from(address.session.session_id,
                                                    uuid.uuid4().hex, context))
            else:
                new = self.owner.creation.create(address.session, uuid.uuid4().hex, context)
            row = session_record(new, self.descriptor)
            granted = self.control.execute(wire.connection, dict(address=row["address"],
                action="acquire", generation=0, previous=None))
            return dict(session=row, control=granted.record())

    def retire(self, wire):
        self.replay.retire(wire)

    def close(self, timeout=DEFAULT_CLEANUP_SECONDS):
        with self._lock:
            self.closed.set()
            active = tuple(self._active)
        for wire, _key in active:
            wire.close()
        self.recovery.close(0)
        self.replay.close()
        return self._cleanup.start().wait(timeout)

    def _release(self):
        for pool in self.pools.values():
            pool.shutdown(wait=True)
        self.ending.wait(None)
        self.workspace.close(None)
        self.recovery.close(None)
