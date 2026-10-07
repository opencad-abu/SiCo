"""Read-only AIVW adapter for the authenticated cdns-ipc session.

This module does not create a listener or own a Virtuoso session.  It starts the
existing ``sico-ai mcp`` stdio client as a short-lived child, which authenticates
to the controller-owned Unix socket with the already delegated session
credentials.  Tokens are never returned, logged, or written to a run artifact.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
import subprocess
from typing import Any, Mapping, Sequence

from .agent.runtime_info import production_python
from sicoentry import compiled_module, native_entry


_READ_ONLY_TOOLS = frozenset(
    {
        "get_context",
        "inspect_schematic",
        "inspect_symbol_ports",
        "inspect_library",
        "inspect_config_binding",
    }
)


class CdnsIpcUnavailable(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class McpToolCall:
    name: str
    arguments: Mapping[str, Any]


@dataclass(frozen=True)
class McpToolResult:
    name: str
    payload: Mapping[str, Any]


def call_read_only_tools(
    calls: Sequence[McpToolCall],
    *,
    environment: Mapping[str, str],
    cad_ai_entry: Path,
    timeout: float,
) -> tuple[McpToolResult, ...]:
    """Execute a bounded read-only batch through the current authenticated session."""
    if timeout <= 0:
        raise CdnsIpcUnavailable("invalid_timeout", "IPC timeout must be positive")
    if not calls:
        raise CdnsIpcUnavailable("invalid_request", "IPC batch must not be empty")
    unknown = sorted({call.name for call in calls} - _READ_ONLY_TOOLS)
    if unknown:
        raise CdnsIpcUnavailable(
            "unsafe_tool", f"IPC batch contains non-read-only tool(s): {unknown}"
        )
    token = _validate_session_environment(environment)
    if compiled_module(__file__):
        try:
            entry = native_entry("aiassistant", environment=environment,
                                 anchor=Path(__file__).resolve().parents[4])
        except ValueError as exc:
            raise CdnsIpcUnavailable("client_unavailable", str(exc)) from exc
        invocation = (str(entry),)
    else:
        raw_entry = cad_ai_entry.expanduser()
        if raw_entry.is_symlink() or not raw_entry.is_file():
            raise CdnsIpcUnavailable(
                "client_unavailable", "authenticated development MCP entry is unavailable")
        entry = raw_entry.resolve()
        invocation = (production_python(require_environment=True), "-s", str(entry))
    messages = []
    identifiers = []
    for index, call in enumerate(calls):
        request_id = f"aivw-read-{index:03d}"
        identifiers.append(request_id)
        messages.append(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": call.name, "arguments": dict(call.arguments)},
            }
        )
    request_text = "".join(
        json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n"
        for item in messages
    )
    command = (
        *invocation,
        "mcp",
        "--request-timeout",
        f"{min(timeout, 120.0):g}",
    )
    child_environment = dict(environment)
    try:
        completed = subprocess.run(
            command,
            input=request_text,
            cwd=entry.parent,
            env=child_environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CdnsIpcUnavailable(
            "ipc_timeout",
            "authenticated Virtuoso inspection timed out and was not retried",
        ) from exc
    except OSError as exc:
        raise CdnsIpcUnavailable(
            "client_unavailable", f"cannot start authenticated Virtuoso client: {exc}"
        ) from exc
    if completed.returncode != 0:
        detail = _safe_process_error(completed.stderr, token)
        raise CdnsIpcUnavailable(
            "client_failed",
            f"authenticated Virtuoso client exited with {completed.returncode}: {detail}",
        )
    responses: dict[str, Mapping[str, Any]] = {}
    for line in completed.stdout.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CdnsIpcUnavailable(
                "invalid_response", "authenticated Virtuoso client returned invalid JSONL"
            ) from exc
        if not isinstance(value, Mapping) or not isinstance(value.get("id"), str):
            raise CdnsIpcUnavailable(
                "invalid_response", "authenticated Virtuoso response has no request ID"
            )
        response_id = str(value["id"])
        if response_id in responses:
            raise CdnsIpcUnavailable(
                "invalid_response", f"duplicate authenticated Virtuoso response: {response_id}"
            )
        responses[response_id] = value
    if set(responses) != set(identifiers):
        raise CdnsIpcUnavailable(
            "incomplete_response", "authenticated Virtuoso response batch is incomplete"
        )
    results = []
    for request_id, call in zip(identifiers, calls):
        response = responses[request_id]
        if isinstance(response.get("error"), Mapping):
            error = response["error"]
            raise CdnsIpcUnavailable(
                "mcp_error", f"{call.name}: {error.get('message', 'MCP request failed')}"
            )
        result = response.get("result")
        if not isinstance(result, Mapping):
            raise CdnsIpcUnavailable(
                "invalid_response", f"{call.name}: MCP result is not an object"
            )
        payload = _decode_tool_payload(call.name, result)
        results.append(McpToolResult(call.name, payload))
    return tuple(results)


def _decode_tool_payload(name: str, result: Mapping[str, Any]) -> Mapping[str, Any]:
    content = result.get("content")
    if not isinstance(content, list) or len(content) != 1 or not isinstance(content[0], Mapping):
        raise CdnsIpcUnavailable(
            "invalid_response", f"{name}: MCP content is incomplete"
        )
    text = content[0].get("text")
    if not isinstance(text, str):
        raise CdnsIpcUnavailable(
            "invalid_response", f"{name}: MCP content is not JSON text"
        )
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CdnsIpcUnavailable(
            "invalid_response", f"{name}: MCP content is invalid JSON"
        ) from exc
    if not isinstance(payload, Mapping):
        raise CdnsIpcUnavailable(
            "invalid_response", f"{name}: inspection payload is not an object"
        )
    if result.get("isError") is True or payload.get("ok") is not True:
        error = payload.get("error")
        code = (
            str(error.get("code"))
            if isinstance(error, Mapping) and error.get("code")
            else str(payload.get("code") or "inspection_error")
        )
        message = (
            str(error.get("message"))
            if isinstance(error, Mapping) and error.get("message")
            else str(payload.get("message") or "inspection failed")
        )
        raise CdnsIpcUnavailable(code, f"{name}: {message}")
    return dict(payload)


def _validate_session_environment(environment: Mapping[str, str]) -> str:
    root_text = _credential(environment, "SICO_AI_RUNTIME", "SICO_CODEX_RUNTIME")
    socket_text = _credential(environment, "SICO_AI_SOCKET", "SICO_CODEX_SOCKET")
    spool_text = _credential(environment, "SICO_AI_SPOOL", "SICO_CODEX_SPOOL")
    token = _credential(environment, "SICO_AI_TOKEN", "SICO_CODEX_TOKEN")
    if not all((root_text, socket_text, spool_text, token)):
        raise CdnsIpcUnavailable(
            "session_credentials_unavailable",
            "current process has no delegated authenticated Virtuoso session",
        )
    root = Path(root_text).expanduser()
    spool = Path(spool_text).expanduser()
    socket_path = Path(socket_text).expanduser()
    try:
        root = root.resolve(strict=True)
        spool = spool.resolve(strict=True)
    except OSError as exc:
        raise CdnsIpcUnavailable(
            "invalid_session_runtime", f"authenticated session runtime is unavailable: {exc}"
        ) from exc
    _require_private_directory(root, "runtime")
    _require_private_directory(spool, "spool")
    if socket_path != root / "bridge.sock":
        raise CdnsIpcUnavailable(
            "invalid_session_runtime", "authenticated socket is outside the session runtime"
        )
    try:
        info = socket_path.lstat()
    except OSError as exc:
        raise CdnsIpcUnavailable(
            "session_unavailable", f"authenticated Virtuoso socket is unavailable: {exc}"
        ) from exc
    if (
        not stat.S_ISSOCK(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        raise CdnsIpcUnavailable(
            "invalid_session_runtime", "authenticated Virtuoso socket is not private"
        )
    return token


def _credential(environment: Mapping[str, str], canonical: str, legacy: str) -> str:
    value = str(environment.get(canonical) or environment.get(legacy) or "").strip()
    return value


def _require_private_directory(path: Path, label: str) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise CdnsIpcUnavailable(
            "invalid_session_runtime", f"authenticated {label} directory is unavailable: {exc}"
        ) from exc
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        raise CdnsIpcUnavailable(
            "invalid_session_runtime", f"authenticated {label} directory is not private"
        )


def _safe_process_error(value: str, token: str) -> str:
    text = value.replace(token, "<redacted>").strip()
    lines = text.splitlines()
    return (lines[-1] if lines else "no diagnostic")[:1000]


__all__ = [
    "McpToolCall",
    "McpToolResult",
    "CdnsIpcUnavailable",
    "call_read_only_tools",
]
