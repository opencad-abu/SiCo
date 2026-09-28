"""Connection-local server request identities shared by native and MCP inputs."""

import json
from hashlib import sha256

from .rpc import RpcError


class InputIdentityConflict(RpcError):
    """A duplicate changes identity; reject without disturbing other children."""


def request_id(value):
    return (type(value) is int and -(2 ** 63) <= value < 2 ** 64
            or isinstance(value, str) and 0 < len(value) <= 200)


class InputRequests:
    def __init__(self, audit):
        self.audit, self.loop = audit, audit.loop
        self.connection, self.connection_id = None, ""
        self.seen, self.closed = {}, set()

    def connect(self):
        nonce = self.loop.input_connection()
        if nonce != self.connection_id:
            self.connection, self.connection_id = self.loop.runtime.rpc, nonce
            self.seen.clear()
            self.closed.clear()

    def check(self, rpc_id, thread, value):
        self.connect()
        if not request_id(rpc_id):
            raise ValueError("Invalid server request id")
        key = (type(rpc_id), rpc_id)
        signature = sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                      allow_nan=False).encode("utf-8")).hexdigest()
        previous = self.seen.get(key)
        if previous and previous != (thread, signature):
            raise InputIdentityConflict("Server input identity changed; inspect before continuing")
        if (thread, *key) in self.closed or previous:
            return None
        if len(self.seen) >= 4096:
            raise RpcError("Server input identity limit reached; reopen the session")
        return key, (thread, signature)

    def remember(self, claim):
        key, value = claim
        self.seen[key] = value

    def discard(self, event):
        """Keep rejected/idle request IDs closed for the lifetime of this connection."""
        params = event.get("params")
        if (not isinstance(params, dict) or not request_id(event.get("id"))
                or not isinstance(params.get("threadId"), str)):
            return True
        self.connect()
        key = (type(event["id"]), event["id"])
        closed = (params["threadId"], *key)
        if key in self.seen or closed in self.closed:
            return False
        self.remember_closed(closed)
        return True

    def remember_closed(self, key):
        if key not in self.closed and len(self.closed) >= 4096:
            raise RpcError("Server input identity limit reached; reopen the session")
        self.closed.add(key)

    def resolved(self, event):
        if event.get("method") != "serverRequest/resolved" or "id" in event:
            return False
        params = event.get("params")
        if (not isinstance(params, dict) or not request_id(params.get("requestId"))
                or not isinstance(params.get("threadId"), str) or not self.loop.runtime):
            return True
        with self.loop.lock:
            self.connect()
            thread, rpc_id = params["threadId"], params["requestId"]
            key = (type(rpc_id), rpc_id)
            previous = self.seen.get(key)
            if previous and previous[0] != thread:
                self.loop._event("codex.input_ignored", {"reason": "foreign_resolved_thread"})
                return True
            if thread != self.loop.thread_id and not previous:
                try:
                    self.loop.child_inputs.current(thread)
                except ValueError:
                    self.loop._event("codex.input_ignored", {
                        "reason": "unmatched_child_resolution"})
                    return True
            closed = (thread, *key)
            self.remember_closed(closed)
            for audit_id, pending in list(self.audit.pending.items()):
                binding = pending.get("binding") or {}
                if (binding.get("thread_id", binding.get("threadId")) == thread
                        and type(pending["rpc_id"]) is type(rpc_id)
                        and pending["rpc_id"] == rpc_id):
                    self.audit.end_inputs("服务已结束此交互；未交付的答复不会重发",
                                          keys=[audit_id], send=False)
        return True
