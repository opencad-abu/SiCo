"""Session-owned registration and public inventory of approved external resources."""

from __future__ import annotations

import time
from copy import deepcopy
from pathlib import Path

from .resource_config import ResourceConfig
from .rpc import RpcError


class ExternalResources:
    def __init__(self, backend, records):
        self.backend, self.config = backend, None
        self.snapshot = {"status": "not_started", "rows": [], "skills": [], "plugins": [],
                         "dependencies": [], "issues": []}
        self.managed, self.installed, self.bound = set(), {}, []
        for event in records:
            if event["kind"] == "codex.thread":
                self.bound = event["payload"].get("capability_roots", [])
            elif event["kind"] == "codex.resources.updated":
                self.snapshot = event["payload"]
                self.managed.update(row["plugin_id"] for row in self.snapshot["rows"]
                                    if row.get("plugin_id"))
                self.installed.update(self.snapshot.get("installed", {}))
        self.published = deepcopy(self.snapshot)

    def publish(self):
        self.snapshot["installed"] = deepcopy(self.installed)
        snapshot = deepcopy(self.snapshot)
        if snapshot != self.published:
            self.backend._event("codex.resources.updated", snapshot)
            self.published = snapshot

    def prepare(self):
        self.config = None
        self.snapshot = {"status": "pending", "configured": True, "rows": [], "skills": [],
                         "plugins": [], "dependencies": [], "issues": []}
        try:
            self.config = ResourceConfig(
                self.backend.provider, str(self.backend.journal.root.parents[2]),
            )
            self.snapshot = {"status": "pending", "manifest": self.config.path,
                             "sha256": self.config.digest, "configured": self.config.configured,
                             "rows": self.config.rows, "skills": [], "plugins": [],
                             "dependencies": [], "issues": []}
            if self.backend.thread_id and self.config.selected_roots() != self.bound:
                raise ValueError("此会话的能力目录已固定；更改 capability roots 后请新建会话")
            if any(r["kind"] == "capability_roots" and r["status"] == "unavailable"
                   for r in self.config.rows):
                raise ValueError("选定的能力目录不可用，请恢复目录或新建会话")
        except ValueError as exc:
            self.snapshot.update(status="unavailable", issues=[str(exc)])
            self.publish()
            raise RpcError(str(exc)) from None
        self.publish()

    def register(self, rpc):
        try:
            self._register(rpc)
        except (ValueError, OSError, RuntimeError, KeyError, TypeError):
            message = "外部资源注册未完成；未启动模型任务，请核对清单和本地依赖"
            self.snapshot.update(status="unavailable", issues=[message])
            self.publish()
            raise RpcError(message) from None

    def _register(self, rpc):
        if not self.config.configured and not self.managed:
            return
        roots = [row["path"] for row in self.config.ready("skill_roots")]
        plugins = self.config.ready("plugins")
        selected = {row["plugin_id"] for row in plugins}
        for key in sorted(self.managed - selected):
            # Use the public config writer: CLI dotted-key overrides do not
            # reliably address a quoted plugin id. This runs before thread load.
            rpc.request("config/value/write", {
                "keyPath": 'plugins."' + key + '".enabled',
                "value": False, "mergeStrategy": "replace",
            }, timeout=3)
        rpc.request("skills/extraRoots/set", {"extraRoots": roots}, timeout=5)
        installed = self.plugin_inventory(rpc) if plugins else []
        deadline = time.monotonic() + 20
        for row in plugins:
            try:
                # Persist ownership before installation, including an uncertain/partial result.
                self.managed.add(row["plugin_id"])
                self.publish()
                fingerprint = [row[k] for k in ("path", "bundle", "manifest_sha256",
                                                "marketplace_sha256")]
                if (self.installed.get(row["plugin_id"]) == fingerprint
                        and any(p.get("id") == row["plugin_id"] and p.get("installed")
                                and p.get("enabled")
                                for p in installed)):
                    row.update(status="registered", message="已注册，等待运行时检查")
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError("Plugin registration time budget exceeded")
                # No account or global installation: native cache/config belong to this journal.
                result = rpc.request("plugin/install", {
                    "marketplacePath": row["path"], "pluginName": row["name"],
                }, timeout=min(10, remaining))
                self.installed[row["plugin_id"]] = fingerprint
                row.update(status="registered", message="已注册，等待运行时检查")
                if result.get("appsNeedingAuth"):
                    row["auth_required"] = True
                    row.update(status="dependency_missing", message="插件应用需要账号授权")
            except (ValueError, OSError, RuntimeError):
                row.update(status="unavailable", message="本地插件注册失败，请核对资源和依赖")
                # Installation may have partially succeeded. Do not load an uncertain plugin.
                raise RpcError("本地插件注册未完成：" + row["id"]) from None
            finally:
                self.publish()

    def plugin_inventory(self, rpc):
        response = rpc.request("plugin/installed", {"cwds": self.marketplace_cwds()}, timeout=5)
        return [item for market in response["marketplaces"] for item in market["plugins"]]

    def marketplace_cwds(self):
        return [self.backend.runtime.cwd, *dict.fromkeys(
            str(Path(r["path"]).parents[2]) for r in self.config.ready("plugins")
        )]

    def inspect(self, rpc):
        issues, skills, plugins, dependencies = [], [], [], []
        deadline = time.monotonic() + 10
        def request(method, params, timeout=3):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError("Resource inspection time budget exceeded")
            return rpc.request(method, params, timeout=min(timeout, remaining))
        try:
            response = request("skills/list", {
                "cwds": [self.backend.runtime.cwd], "forceReload": True,
            }, timeout=5)
            for group in response["data"]:
                available = 512 - len(skills)
                if len(group["skills"]) > available:
                    issues.append("技能清单超过显示上限，仅显示前 512 项")
                for item in group["skills"][:available]:
                    skills.append({key: item.get(key) for key in
                                   ("name", "path", "enabled", "scope", "pluginId")})
                    for dep in (item.get("dependencies") or {}).get("tools", []):
                        dependencies.append({"skill": item["name"], "type": dep.get("type"),
                                             "name": dep.get("value"), "status": "unverified"})
                if group.get("errors"):
                    issues.append("部分技能无法解析，请核对 SKILL.md 格式和目录权限")
            response = request("plugin/installed", {
                "cwds": self.marketplace_cwds(),
            }, timeout=5)
            for market in response["marketplaces"]:
                for item in market["plugins"]:
                    plugins.append({key: item.get(key) for key in
                                    ("id", "name", "enabled", "installed", "localVersion",
                                     "availability", "disabledReason")})
            if response.get("marketplaceLoadErrors"):
                issues.append("部分插件来源不可读取")
            servers, cursor, seen = [], None, set()
            for _ in range(20):
                page = request("mcpServerStatus/list", {
                    "threadId": self.backend.thread_id, "cursor": cursor, "limit": 100,
                }, timeout=2)
                servers.extend(page["data"])
                cursor = page.get("nextCursor")
                if cursor is None:
                    break
                if cursor in seen:
                    raise ValueError("Repeated MCP inventory cursor")
                seen.add(cursor)
            else:
                raise ValueError("MCP inventory exceeds page budget")
            for dep in dependencies:
                matches = [s for s in servers if dep["type"] == "mcp" and s["name"] == dep["name"]]
                if matches:
                    server = matches[0]
                    dep["status"] = ("available" if server.get("runtimeStatus") == "connected"
                                     and not server.get("toolsError") else "unavailable")
                elif dep["type"] == "mcp":
                    dep["status"] = "missing"
            for row in self.config.rows:
                if row.get("load_error"):
                    continue
                path = Path(row["path"])
                if not (path.is_file() if row["kind"] == "plugins" else path.is_dir()):
                    row.update(
                        status="unavailable", message="资源来源已不存在，请恢复后重启会话进程",
                    )
                    row.pop("servers", None)
                    continue
                if row["kind"] == "skill_roots":
                    root = Path(row["path"])
                    found = [s for s in skills if s["enabled"] and
                             Path(s["path"]).resolve().is_relative_to(root)]
                    row.update(
                        status="available" if found else "empty",
                        message=f"已发现 {len(found)} 项技能" if found else "未发现启用的技能",
                    )
                elif row["kind"] == "plugins":
                    item = next((p for p in plugins if p["id"] == row["plugin_id"]), {})
                    if not item.get("installed") or not item.get("enabled"):
                        row.update(status="unavailable", message="运行时未启用此插件")
                        continue
                    else:
                        row.update(status="available", message="已安装并启用")
                    detail = request("plugin/read", {
                        "marketplacePath": row["path"], "pluginName": row["name"],
                    })["plugin"]
                    if (row.get("has_apps") or row.get("has_hooks") or row.get("auth_required")
                            or detail.get("apps") or detail.get("hooks")):
                        row.update(status="dependency_missing",
                                   message="插件已安装；应用授权或 hook 信任状态尚未验证")
                    row["servers"] = [{"name": s["name"], "status": s.get("runtimeStatus"),
                                       "auth": s.get("authStatus"),
                                       "tools": len(s.get("tools", {})),
                                       "failed": bool(s.get("toolsError"))}
                                      for s in servers if s.get("pluginId") == row["plugin_id"]]
                    if any(s["status"] != "connected" or s["failed"] for s in row["servers"]):
                        row.update(
                            status="dependency_missing", message="插件已安装，工具连接尚未就绪",
                        )
                    if len(row["servers"]) < len(detail.get("mcpServers", [])):
                        row.update(status="dependency_missing", message="插件工具尚未被运行时发现")
                else:
                    row.update(
                        status="selected", message="已绑定当前线程；通过原生技能工具按需读取",
                    )
        except (ValueError, OSError, RuntimeError, KeyError, TypeError):
            issues.append("外部资源检查未完成；保留注册信息，请稍后重新检查")
            for row in self.config.rows:
                if not row.get("load_error"):
                    row.update(status="unverified", message="本次检查未完成，可用性尚未确认")
                    row.pop("servers", None)
        self.snapshot.update(status="checked" if not issues else "partial", skills=skills,
                             plugins=plugins, dependencies=dependencies, issues=issues)
        self.publish()
        return deepcopy(self.published)

    def status(self):
        if self.backend.runtime is None:
            self.prepare()
        if self.backend.runtime:
            return self.inspect(self.backend.runtime.rpc)
        return deepcopy(self.published)
