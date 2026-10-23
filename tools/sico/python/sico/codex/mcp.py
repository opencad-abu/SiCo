"""Stdio MCP transport for the existing bound-context registry; no duplicate handlers."""

from __future__ import annotations

import hmac
import json
import os
import socket
import socketserver
import sys
import threading

from sicoenv import read as environment_setting

from ..transport.framing import Connection, strict_json


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(self.server.bridge.timeout)
        connection = Connection(self.request)
        try:
            message = connection.receive()
            if not hmac.compare_digest(str(message.get("token", "")), self.server.bridge.token):
                raise ValueError("Invalid tool transport credential")
            connection.send({"result": self.server.bridge.dispatch(message)})
        except (ValueError, TypeError, OSError, EOFError, KeyError):
            try:
                connection.send({"error": "CAD tool transport unavailable"})
            except OSError:
                pass


class ToolBridge:
    def __init__(self, tools, execute, *, timeout=1800, scope=None, workflow=None,
                 adopt_workflow=None):
        import secrets

        self.tools, self.execute, self.timeout = tools, execute, timeout
        self.scope = scope
        self.workflow = workflow
        self.adopt_workflow = adopt_workflow
        self._workflow_cache = {}
        self._scope_names = frozenset(scope["tools"]) if scope else None
        self._scope_operations = frozenset(scope.get("operations", ())
                                           if scope else ())
        self.token = secrets.token_urlsafe(32)
        # A parent turn may wait for a native child while that child is still
        # calling MCP.  A single-threaded server would deadlock in that case.
        class _ThreadedServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
            daemon_threads = True
            allow_reuse_address = True

        self.server = _ThreadedServer(("127.0.0.1", 0), _Handler)
        self.server.bridge = self
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.05}
        )
        self.thread.start()

    @property
    def port(self):
        return self.server.server_address[1]

    def _canonical(self, name):
        resolve = getattr(self.tools, "resolve", None)
        tool = resolve(name) if callable(resolve) else None
        return getattr(tool, "name", None) or name

    def _profile_candidates(self, name, operation=None):
        """Profiles that could execute this tool/operation, narrowest first."""
        from ..adapters.tool_profiles import PROFILES, host_scope

        try:
            schemas = self.tools.mcp_schemas()
        except (AttributeError, TypeError, ValueError):
            return []
        found = []
        for profile_id in PROFILES:
            try:
                scope = host_scope(profile_id, schemas)
            except (AttributeError, TypeError, ValueError):
                continue
            allowed = (operation in scope.get("operations", ())
                       if operation is not None else name in scope["tools"])
            if allowed:
                found.append((len(scope["tools"]), profile_id))
        return [profile_id for _, profile_id in sorted(found)]

    def _workflow_view(self, workflow_id):
        if workflow_id in self._workflow_cache:
            return self._workflow_cache[workflow_id]
        from cadai.task_group_resolver import resolve
        from cadai.task_group_schema import GroupDeclarationError
        from ..adapters.task_groups import TASK_GROUPS

        catalog = {row["name"]: row for row in self.tools.mcp_schemas()}
        try:
            view = resolve(workflow_id, declarations=TASK_GROUPS, catalog=catalog)
        except (KeyError, GroupDeclarationError):
            view = None
        self._workflow_cache[workflow_id] = view
        return view

    def _workflow_operations(self, workflow_id):
        view = self._workflow_view(workflow_id)
        if view is None:
            return frozenset()
        return frozenset(
            route["operation"]
            for condition in view["conditions"]
            for route in condition["routes"]
            if route.get("operation")
        )

    def _workflow_candidates(self, operation):
        from ..adapters.task_groups import TASK_GROUPS

        candidates = []
        for workflow_id in TASK_GROUPS:
            view = self._workflow_view(workflow_id)
            if view is None or not view["tools"]:
                continue
            if not set(view["tools"]) <= self._scope_names:
                continue
            if operation in self._workflow_operations(workflow_id):
                candidates.append((len(view["tools"]), workflow_id))
        return [workflow_id for _, workflow_id in sorted(candidates)]

    def _workflow_denied(self, name, reason, workflow, operation):
        record = {
            "ok": False, "code": "operation_outside_workflow", "tool": name,
            "profile": (self.scope or {}).get("profile"),
            "workflow": workflow, "operation": operation, "reason": reason,
        }
        candidates = self._workflow_candidates(operation)
        if candidates:
            record["required_workflow"] = candidates[0]
            record["candidate_workflows"] = candidates
            record["next_action"] = (
                "select workflow " + candidates[0] + " in begin_circuit_task")
        else:
            record["next_action"] = (
                "select a task workflow that declares this operation")
        return {"content": [{"type": "text", "text": json.dumps(
            record, ensure_ascii=False)}], "isError": True}

    def _denied(self, name, reason, operation=None):
        # One structured tool error; never a silent pass-through.
        record = {
            "ok": False, "code": "tool_outside_profile", "tool": name,
            "profile": (self.scope or {}).get("profile"), "reason": reason,
        }
        candidates = self._profile_candidates(self._canonical(name), operation)
        if candidates:
            record["required_profile"] = candidates[0]
            record["candidate_profiles"] = candidates
            record["next_action"] = (
                "start a new session with tool profile " + candidates[0])
        text = json.dumps(record, ensure_ascii=False)
        return {"content": [{"type": "text", "text": text}], "isError": True}

    def dispatch(self, message):
        if message.get("method") == "list":
            rows = self.tools.mcp_schemas()
            if self._scope_names is not None:
                rows = [row for row in rows if row["name"] in self._scope_names]
            return {"tools": rows}
        if message.get("method") == "call":
            metadata = message.get("metadata") or {}
            if self._scope_names is not None:
                canonical = self._canonical(message["name"])
                if canonical not in self._scope_names:
                    return self._denied(
                        message["name"],
                        "该工具不在当前会话 profile（服务端校验，与 enabled_tools 无关）",
                        operation=None)
                if canonical == "execute_circuit_operation":
                    arguments = message.get("arguments")
                    operation = (arguments.get("operation")
                                 if isinstance(arguments, dict) else None)
                    if operation is not None and operation not in self._scope_operations:
                        return self._denied(
                            message["name"],
                            f"operation {operation} 的目标工具不在当前会话 profile",
                            operation=operation)
                    active = self.workflow() if callable(self.workflow) else None
                    if (operation is not None and active is not None
                            and operation not in self._workflow_operations(active)):
                        candidates = self._workflow_candidates(operation)
                        adopted = (self.adopt_workflow(candidates[0], operation)
                                   if candidates and callable(self.adopt_workflow)
                                   else None)
                        if not isinstance(adopted, dict) or adopted.get("ok") is not True:
                            return self._workflow_denied(
                                message["name"],
                                f"operation {operation} 不属于当前 workflow {active}",
                                active, operation)
            try:
                return self.execute(message["name"], message.get("arguments", {}), metadata=metadata)
            except TypeError as exc:
                # Keep the small bridge test/fallback API compatible with
                # legacy two-argument executors.
                if "metadata" not in str(exc):
                    raise
                return self.execute(message["name"], message.get("arguments", {}))
        raise ValueError("Unsupported CAD tool transport method")

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


def forward(method, **params):
    try:
        timeout = float(environment_setting(os.environ, "SICO_MCP_TIMEOUT", "1800"))
    except ValueError:
        timeout = 1800.0
    if timeout < 1 or timeout > 3600:
        timeout = 1800.0
    with socket.create_connection(
        ("127.0.0.1", int(environment_setting(os.environ, "SICO_MCP_PORT", ""))), timeout
    ) as sock:
        sock.settimeout(timeout)
        connection = Connection(sock)
        connection.send(
            {"method": method, "token": environment_setting(os.environ, "SICO_MCP_TOKEN", ""), **params}
        )
        result = connection.receive()
        if "error" in result:
            raise ValueError("CAD tool transport unavailable")
        return result["result"]


def main():
    while True:
        raw = sys.stdin.buffer.readline(1024 * 1024 + 1)
        if not raw:
            return 0
        if len(raw) > 1024 * 1024 or not raw.endswith(b"\n"):
            return 2
        try:
            message = strict_json(raw)
        except ValueError:
            return 2
        request_id = message.get("id")
        if request_id is None:
            continue
        try:
            method, params = message.get("method"), message.get("params", {})
            if method == "initialize":
                result = {
                    "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "copilot-context", "version": "1.0"},
                }
            elif method == "tools/list":
                result = forward("list")
            elif method == "tools/call":
                result = forward(
                    "call", name=params["name"], arguments=params.get("arguments", {}),
                    metadata=params.get("_meta") or {},
                )
            elif method == "ping":
                result = {}
            else:
                raise ValueError("Unsupported MCP method")
            response = {"jsonrpc": "2.0", "id": request_id, "result": result}
        except (ValueError, TypeError, OSError, EOFError, KeyError):
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32603,
                    "message": "CAD context tool request failed",
                },
            }
        print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
