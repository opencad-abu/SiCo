"""Bounded native MCP inventory summaries using the M4 byte measurement contract."""

import hashlib
import json


def summaries(servers, previous=()):
    prior = {(r["name"], r.get("plugin_id")): r for r in previous}
    result = []
    for server in servers:
        tools = server.get("tools", {})
        rows = [{key: tool.get(key) for key in ("name", "description", "inputSchema", "annotations")}
                for _, tool in sorted(tools.items())]
        raw = json.dumps(rows, ensure_ascii=False, allow_nan=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        old = prior.get((server["name"], server.get("pluginId")))
        if old and old["sha256"] == digest:
            result.append(old)
            continue
        result.append({"name": server["name"], "plugin_id": server.get("pluginId"),
                       "method": "sico-mcp-bytes.v1", "count": len(rows),
                       "array_compact_bytes": len(raw), "sha256": digest,
                       "changed": bool(old) and old["sha256"] != digest,
                       "prior_sha256": old["sha256"] if old else None})
    return result
