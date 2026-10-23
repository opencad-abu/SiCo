"""Resource identity evidence; content changes never rewrite frozen selections."""

from pathlib import Path
import re

from .schema import KINDS, path_value

PLUGIN_FIELDS = ("plugin_id", "bundle", "manifest_sha256", "marketplace_sha256", "mcp_sha256")


def capture(rows, *, legacy=False, manifest=None):
    if manifest is not None:
        rows = [*rows, *({**r, "kind": "knowledge_sources", "load_error": not Path(r["path"]).exists()}
                         for r in manifest["knowledge_sources"] if r["type"] == "local")]
    evidence = []
    for row in rows:
        if not row.get("enabled", True):
            continue
        path = row["path"]
        item = {"id": row["id"], "kind": row["kind"],
                "resolved_path": path if legacy else str(Path(path).resolve()),
                "available": not row.get("load_error", False), "plugin": None}
        if row["kind"] == "plugins" and item["available"]:
            item["plugin"] = {key: row.get(key) for key in PLUGIN_FIELDS}
        evidence.append(item)
    return evidence


def validate_fingerprints(rows, manifest):
    expected = {(kind, row["id"]) for kind in KINDS for row in manifest[kind] if row["enabled"]}
    expected.update(("knowledge_sources", row["id"]) for row in manifest["knowledge_sources"]
                    if row["type"] == "local" and row["enabled"])
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {
            "id", "kind", "resolved_path", "available", "plugin"
        } or type(row["available"]) is not bool:
            raise ValueError("资源身份快照无效")
        if not isinstance(row["kind"], str) or not isinstance(row["id"], str):
            raise ValueError("资源身份类型无效")
        key = row["kind"], row["id"]
        if key not in expected or key in seen:
            raise ValueError("资源身份快照与选择不一致")
        seen.add(key)
        path_value(row["resolved_path"], resolve=False)
        plugin = row["plugin"]
        if row["kind"] == "plugins" and row["available"]:
            if not isinstance(plugin, dict) or set(plugin) != set(PLUGIN_FIELDS):
                raise ValueError("插件身份快照缺失")
            if not isinstance(plugin["plugin_id"], str) or not plugin["plugin_id"]:
                raise ValueError("插件身份无效")
            path_value(plugin["bundle"], resolve=False)
            for field in ("manifest_sha256", "marketplace_sha256", "mcp_sha256"):
                val = plugin[field]
                if field == "mcp_sha256" and val is None:
                    continue
                if not isinstance(val, str) or re.fullmatch("[a-f0-9]{64}", val) is None:
                    raise ValueError("插件指纹无效")
        elif plugin is not None:
            raise ValueError("资源身份快照包含额外插件信息")
    if seen != expected:
        raise ValueError("资源身份快照不完整")


def compare(before, after):
    prior = {(row["kind"], row["id"]): row for row in before}
    changes = []
    for row in after:
        old = prior.get((row["kind"], row["id"]))
        if old is None:
            raise ValueError("资源身份快照缺少选择项")
        reason, blocked = None, False
        if row["resolved_path"] != old["resolved_path"]:
            reason, blocked = "path_changed", True
        elif row["available"] != old["available"]:
            reason, blocked = ("became_available", True) if row["available"] else ("unavailable", False)
        elif row["plugin"] is not None and old["plugin"] is not None:
            left, right = old["plugin"], row["plugin"]
            if any(left[k] != right[k] for k in ("plugin_id", "bundle")):
                reason, blocked = "plugin_identity_changed", True
            elif left["mcp_sha256"] is not None and left["mcp_sha256"] != right["mcp_sha256"]:
                reason, blocked = "plugin_tools_changed", True
            elif any(left[k] != right[k] for k in ("manifest_sha256", "marketplace_sha256")):
                reason, blocked = "plugin_content_changed", True
            elif left["mcp_sha256"] is None:
                reason = "legacy_tools_unverified"
        if reason:
            changes.append({"id": row["id"], "kind": row["kind"], "reason": reason, "blocked": blocked})
    return changes
