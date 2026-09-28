"""Dispatch one bounded app-server request for the active turn."""

from .rpc import RpcError


def respond(event, method, params, turn_id, parent_result, *, thread_id, input_request, send, progress):
    if input_request(event, turn_id, parent_ended=parent_result is not None):
        return
    matches = (
        params.get("threadId") == thread_id
        and params.get("turnId") == turn_id
        and parent_result is None
    )
    if not matches:
        send({"id": event["id"], "error": {
            "code": -32602, "message": "Request does not belong to the active turn",
        }})
        return
    if method in {"item/commandExecution/requestApproval",
                  "item/fileChange/requestApproval"}:
        # No audit surface is available. Never silently grant an escalation.
        send({"id": event["id"], "result": {"decision": "decline"}})
        progress()
        return
    if method == "item/permissions/requestApproval":
        send({"id": event["id"], "result": {
            "permissions": {"fileSystem": {"entries": []},
                            "network": {"enabled": False}},
            "scope": "turn",
        }})
        progress()
        return
    if method == "item/tool/call":
        send({"id": event["id"], "error": {
            "code": -32601, "message": "Dynamic client tool calls are not registered"
        }})
        return
    send({"id": event["id"], "error": {
        "code": -32601, "message": "Unsupported Codex server request"
    }})
    raise RpcError("Codex requested an unsupported interactive operation")
