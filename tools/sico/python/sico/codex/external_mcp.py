"""Project MCP definitions into native config; secret values stay in process memory."""

import hashlib
import json

from cadai.mcp_server import RESERVED_IDS

from ..installation import python_path
from ..interpreter import cad_python


class ExternalMcp:
    def __init__(self, manifest, settings):
        self.rows = manifest.get("mcp_servers", [])
        self.environment, self.config, self.issues = {}, {}, {}
        self.references = set()
        source = settings.environment
        model_key = settings.options.get("api_key_env", "SICO_API_KEY")
        forbidden = {model_key, "SICO_API_KEY", "SICO_MODEL_KEY", "OPENAI_API_KEY",
                     "ANTHROPIC_API_KEY", "SICO_MCP_TOKEN"}
        for row in self.rows:
            identity = row["id"]
            config = {key: row[key] for key in ("enabled", "required", "startup_timeout_sec",
                      "tool_timeout_sec", "enabled_tools") if key in row}
            # External tools continue through native approval handling.
            config["default_tools_approval_mode"] = "prompt"
            config.setdefault("startup_timeout_sec", 10)
            config.setdefault("tool_timeout_sec", 60)
            refs = (row["env_refs"] if row["transport"] == "stdio" else
                    {"bearer": row["credential_ref"]} if row.get("credential_ref") else {})
            aliases = {}
            for destination, reference in refs.items():
                self.references.add(reference)
                value = source.get(reference, "")
                if reference in forbidden or (value and value == source.get(model_key)):
                    raise ValueError("MCP 不允许引用模型或内置桥凭据")
                alias = "SICO_RESOURCE_" + hashlib.sha256(
                    (identity + ":" + destination).encode()).hexdigest()[:24].upper()
                aliases[destination] = alias
                if row["enabled"]:
                    if not isinstance(value, str) or not value or "\x00" in value or len(value) > 16384:
                        self.issues[identity] = "凭据或环境引用未提供，请在工程服务启动环境中配置"
                    else:
                        self.environment[alias] = value
            if row["transport"] == "stdio":
                config.update(command=cad_python(source), cwd=row["cwd"], args=[
                    "-s", "-c", "from sico.codex.external_mcp_stdio import main; main()",
                    json.dumps({"command": row["command"], "args": row["args"],
                                "refs": aliases}, ensure_ascii=False)],
                    env={"PYTHONPATH": python_path()},
                    env_vars=["LD_LIBRARY_PATH", "TMPDIR", *aliases.values()])
            else:
                config["url"] = row["url"]
                if aliases:
                    config["bearer_token_env_var"] = aliases["bearer"]
            if identity in self.issues:
                if row.get("required"):
                    raise ValueError("必需 MCP 缺少环境引用：" + identity)
                config["enabled"] = False
            self.config[identity] = config

    def register(self, rpc):
        # Replace our projection before loading a thread; never hot-apply to a live turn.
        rpc.request("config/batchWrite", {"edits": [{"keyPath": "mcp_servers",
                    "value": self.config, "mergeStrategy": "replace"}]}, timeout=5)
        rpc.request("config/mcpServer/reload", {}, timeout=10)

    def verify(self, rpc, cwd, *, builtin=()):
        native = rpc.request("config/read", {"includeLayers": False, "cwd": cwd}, timeout=5)
        servers = native["config"].get("mcp_servers", {})
        if set(servers) - set(self.config) - set(builtin):
            raise ValueError("检测到清单之外的 MCP 配置，请移除工程原生配置中的重复入口")
        for name, value in self.config.items():
            actual = servers.get(name, {})
            if (any(actual.get(k, False if k == "required" else None) != v
                    for k, v in value.items()) or actual.get("environment_id") not in (None, "local")
                    or set(actual) - set(value) - {"environment_id"}):
                raise ValueError("MCP 投影受到其他配置覆盖，请核对工程原生配置")

    def statuses(self, servers):
        result = []
        for row in self.rows:
            match = next((s for s in servers if s["name"] == row["id"] and not s.get("pluginId")), {})
            tools = match.get("tools", {})
            status = ("disabled" if not row["enabled"] else "auth_required" if row["id"] in self.issues
                      else "available" if match.get("runtimeStatus") == "connected" and
                      not match.get("toolsError") else "unavailable")
            result.append({"id": row["id"], "kind": "mcp_servers", "status": status,
                "message": self.issues.get(row["id"], "已连接" if status == "available" else
                                             "未启用" if status == "disabled" else "连接未就绪"),
                "tools": sorted(tools)[:512], "tool_count": len(tools),
                "auth": match.get("authStatus", "unknown")})
        return result


def check_identities(rpc, config):
    owners = set(RESERVED_IDS)
    owners.update(row["id"] for row in config.document["manifest"]["mcp_servers"])
    definitions = {}
    for row in config.ready("plugins"):
        detail = rpc.request("plugin/read", {"marketplacePath": row["path"],
                             "pluginName": row["name"]}, timeout=5)["plugin"]
        for name in detail.get("mcpServers", []):
            if name in owners:
                raise ValueError("MCP 服务名称与插件或内置服务冲突：" + name)
            owners.add(name)
        definitions[row["plugin_id"]] = detail.get("skills", [])
    return definitions
