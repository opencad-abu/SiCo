"""Expose the original Assistant MCP surface through the captured source.

The tool definitions and business handlers remain in :mod:`cadai.mcp`.  The
only extra piece needed by Copilot is a source-bound client for the original
Assistant's fixed SKILL operations.  It uses the same authenticated relay and
the same lexical/definition preflight as the Assistant controller; no second
Virtuoso process or unrestricted socket is started here.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

from cadai.circuit_spec_schema import validate
from cadai.controller_artifact import materialize_snapshot_artifact
from cadai.inspection import build_snapshot_request, build_snapshot_skill
from cadai.mcp import TOOLS as MCP_TOOLS
from cadai.mcp import McpServer
from cadai.mcp_call_errors import normalize_tool_result
from cadai.mcp_definitions import ALIASES as MCP_ALIASES
from cadai.mcp_help import mcp_help
from cadai.mcp_live_handler import LIVE_NAMES as LIVE_MODEL_NAMES
from cadai.runtime import MAX_EVAL_BYTES
from cadai.search import search
from cadai.skill_check import require_preflight
from cadai.skill_check_tools import check_skill, read_skill_file
from cadai.skill_diagnostics import carry_output, output_fields
from cadai.skill_reference import (
    SKILL_REFERENCE_TOOL_NAMES,
    SkillReferenceArgumentError,
)
from cadai.socket_server import RequestFailure
from cadai.template_schema import MAX_CAPTURE_BYTES, capture_skill
from cadai.template_schema import arguments as template_arguments
from cadai.tool_help import cds_help

from ..core.contracts import NeedsReconcile, ToolResult
from ..core.read_dependencies import (
    ProjectReadDependencies,
    manual_dependencies,
    reference_dependencies,
    target_dependencies,
)
from ..core.tools import Tool
from ..transport.methods import QueryUnavailable
from .destructive_reviews import skill_gate
from .task_groups import TASK_GROUPS

# Original Assistant tools that read the captured design and its catalogs.
# They change no OA data, so a pending decision about that design (or about the
# PDK) never hides the evidence the decision itself is based on.
DESIGN_EVIDENCE_READS = frozenset({
    "inspect_schematic", "query_schematic", "get_schematic_item",
    "export_schematic_text", "snapshot_schematic", "inspect_symbol_ports",
    "inspect_master_evidence", "inspect_layout", "inspect_config_binding",
    "inspect_cdf", "get_cdf_probe",
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
            if (set(arguments) - {"approval_digest"} != {"code"}
                    or not isinstance(arguments.get("code"), str)):
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
            ok, detail = self._assistant_result(detail)
            if not ok:
                _append_eval_hint(detail)
            return ok, detail
        if method == "load_skill_file":
            if (set(arguments) - {"approval_digest"} != {"path"}
                    or not isinstance(arguments.get("path"), str)):
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
    dependencies = {"Search": reads, "cds_help": manual_dependencies,
                    # 目录帮助与 SKILL 语法检查是纯本地读取：不依赖任何待决事项，
                    # 待答复期间被 ReadGate 拦下只会白耗回合（2026-10-15 真实记录）。
                    "mcp_help": reference_dependencies,
                    "check_skill": reference_dependencies,
                    **dict.fromkeys(SKILL_REFERENCE_TOOL_NAMES, reference_dependencies),
                    **dict.fromkeys(DESIGN_EVIDENCE_READS, target_dependencies)}
    clients, servers = {}, {}

    scope_cache = {}

    def session_scopes():
        """Resolve the journaled session profile and every declared snapshot once.

        Help must describe the surface the session actually serves, not every
        tool the registry can register. The bridge remains the dispatch-side
        authority; this cache only makes the read-only help truthful.
        """
        if not scope_cache:
            from .session_workflow import (
                allowed_workflows, profile_id,
            )
            from .tool_profiles import PROFILES, host_scope

            profile = profile_id(journal)
            schemas = registry.mcp_schemas()
            profiles = {}
            for profile_id_value in PROFILES:
                try:
                    profiles[profile_id_value] = host_scope(profile_id_value, schemas)
                except (AttributeError, TypeError, ValueError):
                    continue
            scope_row = profiles.get(profile)
            scope_cache.update({
                "profile": profile,
                "scope": scope_row,
                "profiles": profiles,
                "allowed_workflows": (
                    allowed_workflows(scope_row, schemas) if scope_row else []),
                "registered": {tool["name"] for tool in schemas},
            })
        from .session_workflow import (
            operations_for, workflow_id,
        )
        active_workflow = workflow_id(journal)
        scope_row = scope_cache["scope"]
        envelope_operations = set(scope_row.get("operations", ())) if scope_row else set()
        workflow_operations = (
            set(operations_for(active_workflow, registry.mcp_schemas()))
            if active_workflow else set())
        active_operations = (
            sorted(envelope_operations & workflow_operations)
            if active_workflow else sorted(envelope_operations))
        return {
            **scope_cache,
            "workflow": active_workflow,
            "active_operations": active_operations,
        }

    def host_availability(name):
        """Facts about this host and the current session profile."""
        state = session_scopes()
        profile, scope, profiles = (
            state["profile"], state["scope"], state["profiles"])
        if name in LIVE_MODEL_NAMES:
            return {"state": "unavailable",
                    "reason": "SiCo 桥不代理控制器实时建模会话"}
        if scope is not None and name in scope["tools"]:
            return {"state": "available"}
        candidates = sorted(
            (len(row["tools"]), profile_id)
            for profile_id, row in profiles.items() if name in row["tools"])
        if candidates:
            return {
                "state": "requires_profile",
                "reason": "该工具不在当前会话 profile",
                "profile": profile,
                "required_profile": candidates[0][1],
                "candidate_profiles": [row[1] for row in candidates],
            }
        if name in state["registered"]:
            return {"state": "unavailable",
                    "reason": "没有已声明 profile 包含该工具"}
        return {"state": "unavailable", "reason": "宿主未注册该工具"}

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

        def execute(arguments, context, name=name, schema=schema):
            captured = context.snapshot.get("cwd")
            local_only = (name in {"Search", "cds_help", "mcp_help", "check_skill"}
                          | SKILL_REFERENCE_TOOL_NAMES)
            if not local_only and (not isinstance(captured, str) or Path(captured).resolve() != workspace.resolve()):
                return ToolResult("needs_reconcile", "Project workspace differs from task source")
            blocked, arguments = skill_gate(registry, name, arguments, context, workspace)
            if blocked is not None:
                return blocked

            def local_result(data):
                if data.get("ok") is not False:
                    return ToolResult(data=data)
                detail = normalize_tool_result(data, name, schema)
                return ToolResult("tool_error", detail["message"], data=detail)

            # These local handlers do not need a Virtuoso client.  Use them
            # directly so document/search/reference tools remain useful in a
            # Copilot session whose editor has gone away.
            try:
                if name == "Search":
                    return local_result(search(arguments, workspace))
                if name == "cds_help":
                    return local_result(cds_help(arguments, workspace))
                if name == "mcp_help":
                    state = session_scopes()
                    try:
                        data = mcp_help(arguments, workspace,
                                        catalog_rows={tool["name"]: tool
                                                      for tool in registry.mcp_schemas()},
                                        availability=host_availability,
                                        groups=TASK_GROUPS,
                                        session={
                                            "profile": state["profile"],
                                            "active_workflow": state["workflow"],
                                            "allowed_workflows": state["allowed_workflows"],
                                            "operations": state["active_operations"],
                                        })
                    except RequestFailure as exc:
                        detail = normalize_tool_result({
                            **exc.data, "code": exc.code, "message": str(exc),
                        }, name, schema)
                        return ToolResult(exc.code, detail["message"], data=detail)
                    return local_result(data)
                if name == "check_skill":
                    return local_result(check_skill(arguments, workspace))
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
            except RequestFailure as exc:
                detail = normalize_tool_result({
                    **exc.data, "code": exc.code, "message": str(exc),
                }, name, schema)
                return ToolResult(exc.code, detail["message"], data=detail)
            except SkillReferenceArgumentError as exc:
                detail = normalize_tool_result({
                    "code": "invalid_arguments", "message": str(exc),
                }, name, schema)
                return ToolResult("invalid_arguments", detail["message"], data=detail)
            except QueryUnavailable as exc:
                return ToolResult(exc.code, str(exc), {"code": exc.code})
            except Exception as exc:  # dispatcher errors must stay bounded
                # 保留真实原因（有界），否则使用者只能看到异常类名而无法自查。
                return ToolResult(
                    "tool_error",
                    "Original Assistant tool failed (%s): %s"
                    % (type(exc).__name__, str(exc)[:300]),
                )
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

    # Hidden compatibility call names restated on this registry; they are
    # never advertised and use exactly the canonical tool's checks.
    for alias, target in MCP_ALIASES.items():
        if registry.resolve(alias) is None:
            registry.alias(alias, target)


_EVAL_NOT_A_FUNCTION_HINT = (
    "该函数不能按名直接调用（常见于 SKILL++ 定义或闭包）：改用 "
    "(funcall (getd '函数名) 参数…)，例如 (funcall (getd 'ddsOpenLibManager))。"
)


def _append_eval_hint(detail):
    """把“eval: not a function”失败补成可操作提示；失败语义与证据不变。"""
    if not isinstance(detail, dict):
        return
    text = f"{detail.get('error', '')} {detail.get('output', '')}"
    if "not a function" in text and "hint" not in detail:
        detail["hint"] = _EVAL_NOT_A_FUNCTION_HINT


__all__ = ["register_assistant_tools"]
