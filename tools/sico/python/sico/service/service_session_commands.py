"""Authorize lifecycle commands while retaining the existing controller operations."""

from ..core.contracts import identifier
from ..transport.framing import ProtocolError
from .service_protocol import exact_fields
from .service_values import json_view

COMMANDS = {"resolve_interrupted", "cancel", "resume", "acknowledge_interrupted", "recover_input", "abandon_input",
            "answer_audit", "elicitation", "steer"}
TASK_COMMANDS = {"cancel", "resume", "acknowledge_interrupted"}


def session_command(workspace, control, address, wire, operation, args, kwargs):
    if operation == "resolve_interrupted":
        if len(args) != 2 or set(kwargs) != {"expected_task"}:
            raise ProtocolError("Reconciliation requires the original task and call identity")
        if not all(isinstance(v, str) for v in (*args, kwargs["expected_task"])):
            raise ProtocolError("Invalid reconciliation identity")
    elif operation in TASK_COMMANDS:
        if args or set(kwargs) != {"expected_task"} or not isinstance(kwargs["expected_task"], str):
            raise ProtocolError("Task command requires its displayed task identity")
    elif operation in {"recover_input", "abandon_input"}:
        if len(args) != 1 or kwargs or not isinstance(args[0], str):
            raise ProtocolError("Input recovery requires its durable input identity")
    elif operation == "answer_audit":
        if len(args) != 5 or kwargs:
            raise ProtocolError("Invalid captured audit answer")
    elif operation == "elicitation":
        if len(args) != 1 or not {"scope"} <= set(kwargs) <= {"scope", "response", "reply_id"}:
            raise ProtocolError("Invalid captured interaction")
    elif operation == "steer":
        if len(args) != 1 or set(kwargs) != {"scope", "request_id"}:
            raise ProtocolError("Invalid captured steering command")
        if not isinstance(args[0], str) or not args[0].strip() or chr(0) in args[0]:
            raise ProtocolError("Invalid captured steering text")
        try:
            identifier(kwargs["request_id"])
        except ValueError as exc:
            raise ProtocolError("Invalid steering request identity") from exc
        scope = kwargs["scope"]
        if not isinstance(scope, dict):
            raise ProtocolError("Steering scope must be a JSON object")
        scope = json_view(scope)
        exact_fields(scope, {"session_id", "runtime_id", "thread_id", "turn_id",
                             "task_id", "context"})
        if not isinstance(scope["context"], dict):
            raise ProtocolError("Steering scope context must be a JSON object")
        # Preserve the immutable frontend request while normalizing its scope.
        kwargs = dict(kwargs)
        kwargs["scope"] = scope

    def execute(lease):
        controller = workspace.controllers.get(address.session.session_id)
        if controller is None or controller.runtime_id != address.session.runtime_id:
            raise ValueError("会话运行实例已变化")
        with controller._lock:
            control.require(lease, wire.connection, wire.control)
            if operation in {"recover_input", "abandon_input"}:
                if controller.recovery is None:
                    raise ValueError("当前会话没有待恢复记录")
                method = "stage" if operation == "recover_input" else "abandon"
                return getattr(controller.recovery, method)(args[0])
            if operation in TASK_COMMANDS:
                if controller._task.get("id", "") != kwargs["expected_task"]:
                    raise ValueError("原任务已变化，操作未执行")
                if operation == "resume" and controller.recovery is not None:
                    controller.recovery.release()
                return getattr(controller, operation)()
            return getattr(controller, operation)(*args, **kwargs)

    if operation == "steer":
        # Admission is protected by the lease, but the native turn/steer receipt
        # is asynchronous. Release the session gate before waiting so takeover,
        # cancellation, and connection cleanup can still make progress. The
        # worker revalidates this same lease immediately before invoking the
        # controller, so a takeover cannot turn a queued command into authority.
        with control.guard(address) as lease:
            control.require(lease, wire.connection, wire.control)
            receipt = workspace.worker._request(execute, lease)
        return receipt.result()

    with control.guard(address) as lease:
        control.require(lease, wire.connection, wire.control)
        return workspace.worker._request(execute, lease).result()
