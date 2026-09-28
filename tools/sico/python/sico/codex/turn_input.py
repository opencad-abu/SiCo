"""Match interactive requests to their live root or registered child turn."""

from .input_requests import InputIdentityConflict, request_id
from .approval_requests import METHODS as APPROVALS, open_request


def accept_input(event, turn_id, *, parent_ended, thread_id, children, audit, admitted, rpc, emit):
    method = event.get("method")
    if method not in {"item/tool/requestUserInput", "mcpServer/elicitation/request", *APPROVALS}:
        return False
    params = event.get("params")
    try:
        if (not isinstance(params, dict) or audit is None
                or not admitted()):
            raise ValueError("inactive_input_task")
        child = None
        if params.get("threadId") == thread_id:
            allowed = ((None, turn_id) if method == "mcpServer/elicitation/request"
                       else (turn_id,))
            if parent_ended or params.get("turnId") not in allowed:
                raise ValueError("foreign_root_turn")
        else:
            child = children.bind(params.get("threadId"), params.get("turnId"))
        if method == "item/tool/requestUserInput":
            audit.open(event, child=child)
        elif method in APPROVALS:
            open_request(audit, event, turn_id, child=child)
        else:
            audit.elicitations.open(event, turn_id, child=child)
    except InputIdentityConflict:
        # The original request still owns this ID. Never send a second wire response.
        emit("codex.input_ignored", {"reason": "conflicting_input_identity"})
    except (ValueError, KeyError, TypeError):
        # Reasons and protocol errors are deliberately bounded; never copy challenge/params.
        if audit is None or audit.requests.discard(event):
            rpc.send({"id": event["id"] if request_id(event.get("id")) else None,
                                  "error": {
                "code": -32602,
                "message": "Input request does not match a live registered turn"}})
        emit("codex.input_ignored", {"reason": "unmatched_or_invalid_input"})
    return True


def idle_request(event, *, active, audit, rpc, emit):
    method = event.get("method")
    if active or method not in {"mcpServer/elicitation/request",
                                     "item/tool/requestUserInput", *APPROVALS}:
        return False
    if audit is not None:
        if not audit.requests.discard(event):
            return True
    response = ({"result": {"action": "cancel"}} if method == "mcpServer/elicitation/request"
                else {"error": {"code": -32602, "message": "No active task for input request"}})
    rpc.send({"id": event["id"], **response})
    emit("codex.elicitation.unsupported", {"reason": "空闲会话没有可绑定的执行任务"})
    return True
