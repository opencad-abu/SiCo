"""Normalize source findings without weakening the underlying audit rules."""

from __future__ import annotations

import hashlib
import re

from a_philosophy_exports import surface_digest
from a_philosophy_sources import read_python


def exact(gate, message, *, fingerprint=None):
    key = hashlib.sha256(message.encode()).hexdigest()
    return dict(id=f"{gate}:{key}", gate=gate, message=message,
                fingerprint=fingerprint or key)


def metric(gate, key, message, value, limit):
    return dict(id=f"{gate}:{key}", gate=gate, message=message, value=value, limit=limit)


def a_findings(root, report):
    findings = []
    checks = {message: name for name, messages in report["checks"].items() for message in messages}
    for message in report["errors"]:
        match = re.fullmatch(r"unbaselined file exceeds 500 lines: (.+) \((\d+); tracked\)", message)
        if match:
            path, lines = match.groups()
            findings.append(metric("a", f"size:{path}", message, int(lines), 500))
        else:
            fingerprint = None
            if checks.get(message) == "inventory":
                module = message.rsplit(": ", 1)[-1]
                source = root / "tools/ai/python" / (module.replace(".", "/") + ".py")
                if source.is_file():
                    fingerprint = hashlib.sha256(source.read_bytes()).hexdigest()
            findings.append(exact("a", message, fingerprint=fingerprint))
        findings[-1]["check"] = checks.get(message, "discovery")
    for candidate in report["compatibility_candidates"]:
        if candidate["review_state"] == "reviewed":
            continue
        path = candidate["path"]
        findings.append(dict(
            id=f"reviews:{path}", gate="reviews", path=path,
            message=f"{candidate['review_state']} compatibility review: {path}",
            fingerprint=surface_digest(read_python(root / path)),
        ))
    return findings


def mcp_findings(report, budget, result):
    findings = {}
    for message in result["errors"]:
        match = re.fullmatch(r"(canonical|host): array bytes (\d+) grow beyond frozen (\d+)", message)
        profile = re.match(r"profile ([^:]+): (\d+) B (?:grows beyond|exceeds budget)", message)
        if match:
            layer, value, limit = match.groups()
            row = metric("mcp", f"layer:{layer}:bytes", message, int(value), int(limit))
        elif profile:
            name, value = profile.groups()
            row = metric("mcp", f"profile:{name}:bytes", message, int(value), budget["profiles"][name]["budget"])
        else:
            row = exact("mcp", message)
        findings[row["id"]] = row
    # Existing embedded allowances must be visible as debt, even when the raw
    # budget checker returns passed. The governance registry supplies deadlines.
    layer = report["layers"].get("canonical_wire", report["layers"]["canonical"])
    tools = {r["name"]: r for r in layer["measurement"]["tools"]}
    for debt in budget["debt"]["tools"]:
        name = debt["tool"]
        if name in tools and tools[name]["compact_bytes"] > debt["limit"]:
            row = metric("mcp", f"tool:{name}:bytes", f"tool {name} exceeds its class hard limit",
                         tools[name]["compact_bytes"], debt["limit"])
            findings[row["id"]] = row
    for profile in report["tool_profiles"]["profiles"]:
        name = profile["id"]
        limit = budget["profiles"].get(name, {}).get("budget")
        if limit is not None and profile["bytes"] > limit:
            row = metric("mcp", f"profile:{name}:bytes", f"profile {name} exceeds its target budget",
                         profile["bytes"], limit)
            findings[row["id"]] = row
    return list(findings.values())
