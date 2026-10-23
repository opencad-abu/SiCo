"""Bounded model-free native checks in disposable homes, separate from task RPC."""

import json
from pathlib import Path
import shutil
import tempfile
import time

from .external_mcp import ExternalMcp, check_identities
from .resource_config import ResourceConfig
from .resource_registration import register
from .resource_skills import apply, dependencies, inventory, preview
from .resource_metrics import summaries
from .rpc import AppServer
from ..agent_resources.manifest import default_path
from ..agent_resources.schema import require_supported, upgrade_manifest
from ..agent_resources.snapshot import digest
from cadai.json_values import strict_json


class CheckError(ValueError):
    """A bounded local diagnostic that is safe to return to the frontend."""


class ProbeRpc(AppServer):
    def __init__(self, command, environment, cwd, cancelled):
        self.cancelled, self.deadline = cancelled, time.monotonic() + 45
        super().__init__(command, environment, cwd)

    def _next(self, timeout):
        if self.cancelled():
            raise CheckError("资源检查已取消；已发生的工具效果不会撤销")
        if time.monotonic() >= self.deadline:
            raise CheckError("资源检查超过 45 秒；调用结果可能未确认，请勿自动重试")
        return super()._next(timeout)


def servers(rpc, thread_id):
    result, cursor, seen = [], None, set()
    for _ in range(20):
        page = rpc.request("mcpServerStatus/list", {"threadId": thread_id, "cursor": cursor,
                            "limit": 100}, timeout=5)
        result.extend(page["data"])
        cursor = page.get("nextCursor")
        if cursor is None:
            return result
        if cursor in seen:
            break
        seen.add(cursor)
    raise ValueError("MCP 检查目录超过分页上限")


def call_tool(rpc, thread, native, call, external):
    if not isinstance(call, dict) or set(call) != {"server", "tool", "arguments"}:
        raise ValueError("检查调用字段无效")
    if isinstance(call["arguments"], str):
        if len(call["arguments"].encode()) > 16384:
            raise CheckError("调用参数超过 16 KiB 上限，未执行工具")
        try:
            call = {**call, "arguments": strict_json(call["arguments"])}
        except ValueError:
            raise CheckError("调用参数必须为有效 JSON 对象，未执行工具") from None
    if not isinstance(call["arguments"], dict) or len(json.dumps(call).encode()) > 16384:
        raise CheckError("检查调用参数无效或超过上限，未执行工具")
    selected = next((r for r in external.rows if r["id"] == call["server"] and r["enabled"]), None)
    if selected is None:
        raise CheckError("检查调用只允许清单内已启用的独立 MCP，未执行工具")
    server = next((s for s in native if s["name"] == call["server"] and not s.get("pluginId")), {})
    if call["tool"] not in server.get("tools", {}):
        raise CheckError("检查工具不在已发现的独立 MCP 目录中，未执行工具")
    result = rpc.request("mcpServer/tool/call", {"threadId": thread, **call}, timeout=10)
    def safe(value):
        if isinstance(value, str):
            for secret in external.environment.values():
                value = value.replace(secret, "[credential]")
            return value[:8192]
        if isinstance(value, list):
            return [safe(v) for v in value[:32]]
        if isinstance(value, dict):
            return {safe(k): safe(v) for k, v in value.items()}
        return value
    text = json.dumps(safe(result), ensure_ascii=False)
    if len(text.encode()) > 16384:
        raise CheckError("工具已调用，结果超过 16 KiB 显示上限")
    return text


def check(project, manifest, settings, executable, cancelled, call=None, skill=None):
    manifest = upgrade_manifest(manifest)
    require_supported(manifest)
    config = ResourceConfig({"path": str(default_path(project)), "source": "project",
                             "revision": digest(manifest), "manifest": manifest})
    external = ExternalMcp(manifest, settings)
    if cancelled():
        raise CheckError("资源检查已取消")
    directory, rpc = tempfile.mkdtemp(prefix="sico-resource-check-"), None
    try:
        environment = {key: value for key, value in settings.environment.items()
                       if key in {"PATH", "LANG", "LC_ALL", "LD_LIBRARY_PATH"}}
        environment.update(HOME=directory, CODEX_HOME=directory, TMPDIR=directory,
                           PYTHONNOUSERSITE="1", **external.environment)
        options = {"model": "sico-resource-check", "model_provider": "sico_check",
                   "model_providers.sico_check.name": "Isolated resource check",
                   "model_providers.sico_check.base_url": "http://127.0.0.1:9/v1",
                   "model_providers.sico_check.wire_api": "responses",
                   "analytics.enabled": False, "check_for_update_on_startup": False}
        command = [executable, "app-server", "--listen", "stdio://"]
        for key, value in options.items():
            command.extend(["-c", key + "=" + json.dumps(value)])
        rpc = ProbeRpc(command, environment, str(project), cancelled)
        try:
            register(rpc, config, str(project), set(), {})
            definitions = check_identities(rpc, config)
            apply(rpc, config, str(project), definitions)
            external.register(rpc)
            external.verify(rpc, str(project))
            thread = rpc.request("thread/start", {"cwd": str(project), "ephemeral": True,
                "model": "sico-resource-check", "modelProvider": "sico_check",
                "approvalPolicy": "never", "sandbox": "read-only"})["thread"]["id"]
            native = servers(rpc, thread)
            skills = inventory(rpc, str(project), definitions)
            value = {"skills": skills, "dependencies": dependencies(skills, native, {
                         k for k, v in environment.items() if v and not k.startswith("SICO_RESOURCE_")}),
                     "mcp_servers": external.statuses(native), "call_result": None,
                     "mcp_metrics": summaries(native), "skill_preview": None}
            if call is not None:
                value["call_result"] = call_tool(rpc, thread, native, call, external)
            if skill is not None:
                value["skill_preview"] = preview(skills, skill)
            return value
        finally:
            try:
                rpc.close()
            except (OSError, RuntimeError):
                (Path(directory) / "cleanup.json").write_text(json.dumps({
                    "status": "unconfirmed", "supervisor_pid": rpc.child.pid}))
                raise CheckError("临时进程清理未确认，私有核对目录：" + directory) from None
            shutil.rmtree(directory)
    except BaseException:
        # Failed initialization can also mean failed cleanup; retain its isolated home.
        if rpc is None:
            raise CheckError("检查进程未成功初始化，私有核对目录：" + directory) from None
        raise
