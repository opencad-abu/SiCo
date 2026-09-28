"""Explicit external resources; never import a user's complete Codex home."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from cadai.env_names import value as env_value

from ..transport.framing import strict_json
from ..storage.roots import state_root

KINDS = ("skill_roots", "plugins", "capability_roots")
MAX_MANIFEST = 65536
NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}\Z")


def path_value(value):
    if (not isinstance(value, str) or not value or len(value) > 4096
            or any(ord(c) < 32 for c in value) or not Path(value).is_absolute()):
        raise ValueError("外部资源路径必须是绝对路径")
    return str(Path(value).resolve())


def read_object(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_MANIFEST + 1)
    if len(raw) > MAX_MANIFEST:
        raise ValueError("外部资源清单超过 64 KiB")
    value = strict_json(raw)
    if not isinstance(value, dict):
        raise ValueError("外部资源清单必须是 JSON 对象")
    return value, hashlib.sha256(raw).hexdigest()


def validate_manifest(value):
    if set(value) - {"version", *KINDS} or type(value.get("version")) is not int:
        raise ValueError("外部资源清单字段无效")
    if value["version"] != 1:
        raise ValueError("不支持此外部资源清单版本")
    result, ids = {}, set()
    for kind in KINDS:
        rows = value.get(kind, [])
        if not isinstance(rows, list) or len(rows) > 32:
            raise ValueError("每类外部资源最多 32 项")
        result[kind] = []
        for row in rows:
            fields = {"id", "path", "source", "access"}
            if kind == "plugins":
                fields |= {"name"}
            if not isinstance(row, dict) or set(row) != fields:
                raise ValueError("外部资源条目字段不完整或不支持")
            key = row["id"]
            if not isinstance(key, str) or not NAME.fullmatch(key) or key in ids:
                raise ValueError("外部资源 ID 无效或重复")
            ids.add(key)
            access = "read" if kind == "skill_roots" else "execute"
            if row["source"] not in ("user", "project") or row["access"] != access:
                raise ValueError("外部资源来源或使用范围无效")
            if kind == "plugins" and (
                not isinstance(row["name"], str) or not NAME.fullmatch(row["name"])
            ):
                raise ValueError("插件名称无效")
            result[kind].append({**row, "path": path_value(row["path"])})
    return result


class ResourceConfig:
    def __init__(self, settings, cwd):
        options = getattr(settings, "options", {})
        environment = getattr(settings, "environment", {})
        explicit = options.get("external_resources") or env_value(environment, "EXTERNAL_RESOURCES")
        self.path = path_value(explicit) if explicit else str(
            state_root(cwd) / "ai/agent-resources.json"
        )
        self.digest, self.rows = "", []
        self.selection = {kind: [] for kind in KINDS}
        self.configured = bool(explicit) or Path(self.path).exists()
        if not self.configured:
            return
        try:
            value, self.digest = read_object(self.path)
            self.selection = validate_manifest(value)
        except (ValueError, OSError, RuntimeError):
            raise ValueError("外部资源清单不可读或格式无效：" + self.path) from None
        identities, markets = set(), {}
        for kind, entries in self.selection.items():
            for entry in entries:
                row = {**entry, "kind": kind, "status": "pending", "message": "等待检查"}
                self.rows.append(row)
                path = Path(entry["path"])
                try:
                    if kind == "plugins":
                        self.plugin(row)
                        if (row["plugin_id"] in identities or
                                markets.get(row["marketplace"], row["path"]) != row["path"]):
                            raise ValueError("Ambiguous plugin identity")
                        identities.add(row["plugin_id"])
                        markets[row["marketplace"]] = row["path"]
                    elif not path.is_dir() or not os.access(path, os.R_OK | os.X_OK):
                        raise ValueError("资源目录不存在或不可读取")
                except (OSError, ValueError, KeyError, TypeError, AttributeError):
                    row.update(
                        status="unavailable", message="资源不可用，请核对目录、清单和访问权限",
                        load_error=True,
                    )

    @staticmethod
    def plugin(row):
        """Accept only one selected local bundle; no git/npm/remote resolution."""
        path = Path(row["path"])
        if path.name != "marketplace.json" or path.parent.name != "plugins":
            raise ValueError("Expected a .agents/plugins/marketplace.json file")
        if path.parent.parent.name != ".agents":
            raise ValueError("Invalid marketplace location")
        market, fingerprint = read_object(path)
        if not isinstance(market.get("name"), str) or not NAME.fullmatch(market["name"]):
            raise ValueError("Invalid marketplace name")
        matches = [p for p in market.get("plugins", []) if p.get("name") == row["name"]]
        if len(matches) != 1:
            raise ValueError("Selected plugin missing or ambiguous")
        source = matches[0]["source"]
        if source.get("source") != "local" or not isinstance(source.get("path"), str):
            raise ValueError("Only local plugin bundles are supported")
        bundle = (path.parents[2] / source["path"]).resolve()
        manifest, bundle_hash = read_object(bundle / ".codex-plugin/plugin.json")
        if manifest.get("name") != row["name"]:
            raise ValueError("Plugin identity mismatch")
        row.update(plugin_id=row["name"] + "@" + market["name"], marketplace=market["name"],
                   bundle=str(bundle), marketplace_sha256=fingerprint, manifest_sha256=bundle_hash)
        row["has_apps"] = bool(manifest.get("apps") or (bundle / ".app.json").exists())
        row["has_hooks"] = bool(manifest.get("hooks") or (bundle / "hooks/hooks.json").exists())

    def selected_roots(self):
        return sorted([{"id": row["id"], "location": {"type": "environment",
                        "environmentId": "local", "path": row["path"]}}
                       for row in self.selection["capability_roots"]], key=lambda row: row["id"])

    def ready(self, kind):
        return [row for row in self.rows if row["kind"] == kind and not row.get("load_error")]
