"""Expose the original Assistant MCP surface through the captured source.

The tool definitions and business handlers remain in :mod:`cadai.mcp`.  The
only extra piece needed by Copilot is a source-bound client for the original
Assistant's fixed SKILL operations.  It uses the same authenticated relay and
the same lexical/definition preflight as the Assistant controller; no second
Virtuoso process or unrestricted socket is started here.
"""

from __future__ import annotations

import json
import hashlib
import os
from types import SimpleNamespace
from pathlib import Path

from cadai.circuit_spec_schema import validate
from cadai.controller_artifact import materialize_snapshot_artifact
from cadai.mcp import TOOLS as MCP_TOOLS, McpServer
from cadai.runtime import MAX_EVAL_BYTES
from cadai.inspection import build_snapshot_request, build_snapshot_skill
from cadai.search import search
from cadai.tool_help import tool_help
from cadai.skill_check_tools import check_skill
from cadai.skill_reference import SKILL_REFERENCE_TOOL_NAMES
from cadai.skill_check import require_preflight
from cadai.skill_check_tools import read_skill_file
from cadai.template_schema import arguments as template_arguments
from cadai.template_schema import capture_skill
from cadai.template_schema import MAX_CAPTURE_BYTES
from cadai.socket_server import RequestFailure
from cadai.skill_diagnostics import carry_output, output_fields

from ..core.contracts import NeedsReconcile, ToolResult
from ..core.read_dependencies import (
    ProjectReadDependencies,
    manual_dependencies,
    reference_dependencies,
    target_dependencies,
)
from ..core.tools import Tool
from ..transport.methods import QueryUnavailable

# Original Assistant tools that read the captured design and its catalogs.
# They change no OA data, so a pending decision about that design (or about the
# PDK) never hides the evidence the decision itself is based on.
DESIGN_EVIDENCE_READS = frozenset({
    "inspect_schematic", "query_schematic", "get_schematic_item",
    "export_schematic_text", "snapshot_schematic", "inspect_symbol_ports",
    "inspect_layout", "inspect_config_binding", "inspect_cdf", "get_cdf_probe",
    "inspect_library", "list_libraries", "inspect_window", "list_windows",
})


class _AssistantClient:
    """McpServer client bound to one Copilot target and workspace."""

    guard_legacy_workflows = True

    def __init__(self, broker, context, workspace, spool):
        self.broker = broker
        self.context = context
        self.workspace = Path(workspace).resolve()
        self.runtime = SimpleNamespace(spool=Path(spool).resolve())
        self.runtime.spool.mkdir(mode=0o700, parents=True, exist_ok=True)

    def close(self):
        return None

    def pdk_entry_context(self):
        return None

    @staticmethod
    def _assistant_result(detail):
        """Unwrap the relay envelope into the original Assistant result."""
        if not isinstance(detail, dict):
            return False, {"code": "invalid_result", "message": "Assistant relay returned a non-object"}
        if detail.get("ok") is True and isinstance(detail.get("result"), dict):
            detail = detail["result"]
        return detail.get("ok") is True, detail

    def call(self, method, arguments):
        if not isinstance(arguments, dict):
            return False, {"code": "invalid_params", "message": "arguments must be an object"}
        if method.startswith("live_model_"):
            return False, {
                "code": "live_unavailable",
                "message": (
                    "live modeling is owned by the controller session; "
                    "this source-bound Copilot relay has no delegated live worker"
                ),
            }
        if method in {"eval_skill", "eval_skill_native"}:
            if set(arguments) != {"code"} or not isinstance(arguments["code"], str):
                return False, {"code": "invalid_params", "message": "code is required"}
            source = arguments["code"].encode("utf-8")
            if len(source) > MAX_EVAL_BYTES:
                return False, {"code": "payload_too_large", "message": "SKILL source exceeds the size limit"}
            try:
                require_preflight(source)
            except Exception as exc:
                detail = getattr(exc, "detail", None)
                return False, {
                    "code": getattr(exc, "code", "skill_preflight_failed"),
                    "message": str(exc),
                    **({"preflight": detail} if isinstance(detail, dict) else {}),
                }
            detail = self.broker.assistant_call(self.context, method, arguments["code"])
            return self._assistant_result(detail)
        if method == "load_skill_file":
            if set(arguments) != {"path"} or not isinstance(arguments["path"], str):
                return False, {"code": "invalid_params", "message": "path is required"}
            try:
                path, source = read_skill_file(arguments["path"], self.workspace)
                require_preflight(source, source_name=str(path))
            except Exception as exc:
                detail = getattr(exc, "detail", None)
                return False, {
                    "code": getattr(exc, "code", "file_unavailable"),
                    "message": str(exc),
                    **({"preflight": detail} if isinstance(detail, dict) else {}),
                }
            detail = self.broker.assistant_call(self.context, method, str(path))
            return self._assistant_result(detail)
        if method == "snapshot_schematic":
            captured = {}
            try:
                build_snapshot_request(arguments)
                artifact_path = self.runtime.spool / ("snapshot-data-" + os.urandom(12).hex() + ".jsonl")
                code = build_snapshot_skill(arguments, str(artifact_path))
                os.close(os.open(artifact_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
                ok, detail = self.call("eval_skill", {"code": code})
                if not ok:
                    return ok, detail
                captured = output_fields(detail)
                metadata = json.loads(detail.get("value", "{}"))
                path = Path(metadata.get("artifact_path", ""))
                if path != artifact_path or not path.is_file():
                    raise ValueError("snapshot artifact path is invalid")
                raw = path.read_bytes()
                return True, carry_output({"ok": True, "artifact": {
                    "path": str(path), "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
                }}, captured)
            except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
                return False, carry_output({"code": "snapshot_error", "message": str(exc)}, captured)
        if method == "capture_circuit_template":
            captured = {}
            try:
                checked = template_arguments("extract_circuit_templates", arguments)
                artifact_path = self.runtime.spool / ("template-data-" + os.urandom(12).hex() + ".jsonl")
                code = capture_skill(checked, str(artifact_path))
                # aiTplCapture owns creation and deliberately refuses existing files.
                ok, detail = self.call("eval_skill", {"code": code})
                captured = output_fields(detail)
                try:
                    if not ok:
                        return ok, detail
                    return True, materialize_snapshot_artifact(
                        detail, artifact_path, self.runtime.spool, self.runtime.spool,
                        artifact_prefix="template-data-", max_bytes=MAX_CAPTURE_BYTES,
                    )
                finally:
                    artifact_path.unlink(missing_ok=True)
            except (OSError, TypeError, ValueError, RequestFailure) as exc:
                return False, carry_output({"code": "template_capture_error", "message": str(exc)}, captured)
        return False, {"code": "unsupported_method", "message": "Assistant method is not registered"}


def register_assistant_tools(registry, broker, journal, workspace):
    """Register every original MCP name not already provided by shared adapters.

    ``broker`` and ``journal`` are accepted as part of the adapter contract so
    the registration remains tied to the captured session.  The original MCP
    server owns local handlers and validation; no second implementation is
    created here.
    """

    existing = {tool["name"] for tool in registry.schemas()}
    workspace = Path(workspace)
    reads = ProjectReadDependencies(workspace)
    dependencies = {"Search": reads, "tool_help": manual_dependencies,
                    **dict.fromkeys(SKILL_REFERENCE_TOOL_NAMES, reference_dependencies),
                    **dict.fromkeys(DESIGN_EVIDENCE_READS, target_dependencies)}
    clients, servers = {}, {}

    def server_for(context):
        record = context.record()
        key = ":".join(
            (record["instance_id"], record["generation"], record["target_id"])
        )
        if key not in servers:
            client = _AssistantClient(broker, context, workspace, journal.directory / "assistant-spool")
            clients[key] = client
            servers[key] = McpServer(client, workspace=workspace)
        return clients[key], servers[key]

    for definition in MCP_TOOLS:
        name = definition["name"]
        if name in existing:
            continue
        schema = definition["inputSchema"]

        def checked(arguments, schema=schema):
            validate(arguments, schema)

        def execute(arguments, context, name=name):
            captured = context.snapshot.get("cwd")
            local_only = name in {"Search", "tool_help", "check_skill"} | SKILL_REFERENCE_TOOL_NAMES
            if not local_only and (not isinstance(captured, str) or Path(captured).resolve() != workspace.resolve()):
                return ToolResult("needs_reconcile", "Project workspace differs from task source")
            # These local handlers do not need a Virtuoso client.  Use them
            # directly so document/search/reference tools remain useful in a
            # Copilot session whose editor has gone away.
            try:
                if name == "Search":
                    data = search(arguments, workspace)
                    return ToolResult("ok" if data.get("ok") else "tool_error", data=data)
                if name == "tool_help":
                    data = tool_help(arguments, workspace)
                    return ToolResult("ok" if data.get("ok") else "tool_error", data=data)
                if name == "check_skill":
                    data = check_skill(arguments, workspace)
                    return ToolResult("ok" if data.get("ok") else "tool_error", data=data)
                if name in SKILL_REFERENCE_TOOL_NAMES:
                    _, reference_server = server_for(context)
                    data = reference_server.skill_reference.call(name, arguments)
                    return ToolResult(data=data)
                client, server = server_for(context)
                response = server.handle({
                    "jsonrpc": "2.0",
                    "id": "copilot-legacy",
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments},
                })
            except NeedsReconcile:
                raise
            except QueryUnavailable as exc:
                return ToolResult(exc.code, str(exc), {"code": exc.code})
            except Exception as exc:  # dispatcher errors must stay bounded
                return ToolResult("tool_error", "Original Assistant tool failed (%s)" % type(exc).__name__)
            if "error" in response:
                return ToolResult("invalid_arguments", response["error"].get("message", "Invalid arguments"),
                                  data=response["error"].get("data"))
            result = response.get("result") or {}
            content = result.get("content") or []
            if not content or not isinstance(content[0], dict) or not isinstance(content[0].get("text"), str):
                return ToolResult("tool_error", "Original Assistant returned an invalid result")
            try:
                data = json.loads(content[0]["text"])
            except (TypeError, ValueError):
                return ToolResult("tool_error", "Original Assistant returned non-JSON data")
            return ToolResult("tool_error" if result.get("isError") else "ok", data=data)

        registry.register(
            Tool(
                name,
                definition["description"],
                schema,
                checked,
                execute,
                execution_domain="local" if definition.get("annotations", {}).get("readOnlyHint") else "virtuoso",
                annotations=definition.get("annotations", {}),
                effect="read" if name in dependencies else "unknown",
                input_dependencies=dependencies.get(name),
            )
        )


__all__ = ["register_assistant_tools"]
