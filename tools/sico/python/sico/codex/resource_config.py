"""Explicit external resources; never import a user's complete Codex home."""

from __future__ import annotations

import os
from pathlib import Path

from ..agent_resources.manifest import read_object
from ..agent_resources.schema import KINDS, NAME, require_supported
from ..agent_resources.snapshot import digest, document


class ResourceConfig:
    def __init__(self, selected):
        self.document = document(selected)
        require_supported(self.document["manifest"])
        self.rows = []
        if not self.configured:
            return
        identities, markets = set(), {}
        for kind, entries in self.selection.items():
            for entry in entries:
                row = {**entry, "kind": kind, "status": "pending", "message": "等待检查"}
                self.rows.append(row)
                if not row["enabled"]:
                    row.update(status="disabled", message="未启用")
                    continue
                path = Path(entry["path"])
                try:
                    if kind == "plugins":
                        self.plugin(row)
                    elif not path.is_dir() or not os.access(path, os.R_OK | os.X_OK):
                        raise ValueError("资源目录不存在或不可读取")
                except (OSError, ValueError, KeyError, TypeError, AttributeError):
                    row.update(
                        status="unavailable", message="资源不可用，请核对目录、清单和访问权限",
                        load_error=True,
                    )
                if kind == "plugins" and not row.get("load_error"):
                    if (row["plugin_id"] in identities or
                            markets.get(row["marketplace"], row["path"]) != row["path"]):
                        raise ValueError("插件身份或 marketplace 名称冲突，请核对资源选择")
                    identities.add(row["plugin_id"])
                    markets[row["marketplace"]] = row["path"]

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
        mcp = bundle / ".mcp.json"
        row["mcp_sha256"] = read_object(mcp)[1] if mcp.exists() else digest(None)

    @property
    def path(self):
        return self.document["path"]

    @property
    def digest(self):
        return self.document["revision"] or ""

    @property
    def configured(self):
        return self.document["revision"] is not None

    @property
    def selection(self):
        return {kind: self.document["manifest"][kind] for kind in KINDS}

    def selected_roots(self):
        return sorted([{"id": row["id"], "location": {"type": "environment",
                        "environmentId": "local", "path": row["path"]}}
                       for row in self.selection["capability_roots"] if row["enabled"]],
                      key=lambda row: row["id"])

    def ready(self, kind):
        return [row for row in self.rows if row["kind"] == kind
                and row["enabled"] and not row.get("load_error")]
