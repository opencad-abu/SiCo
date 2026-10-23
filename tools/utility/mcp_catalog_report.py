"""Formal offline MCP catalog report for the SiCo tool-surface work (M0/M4).

Run from the repository root; it imports source only and touches no EDA,
service, session or model:

    /software/pkgs/python/3.9.13/bin/python3 \\
        tools/utility/mcp_catalog_report.py --output /path/report.json

Optional inputs:

    --compare PATH  diff the new report against an earlier report JSON

The report fixes the measurement methodology (``sico-mcp-bytes.v1``, see
``mcp_catalog_measure``) and reports every layer separately: canonical
definitions, canonical MCP fields, the assembled SiCo host projection, the
current per-consumer exposure, the runtime task-group closures and the tool
profiles (the declarations are read from the owning domain modules, never
from a copy).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import mcp_catalog_hosts as hosts
from mcp_catalog_measure import METHOD, WIRE_FIELDS, compare, measurement

CONTRACT = "sico.mcp.catalog_report.v1"
GROUPS_CONTRACT = "sico.mcp.task_groups.v1"
PROFILES_CONTRACT = "sico.mcp.tool_profiles.v1"


def _git(root: Path, *arguments: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip()


def _layer(kind: str, source: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"kind": kind, "source": source, "measurement": measurement(rows)}


def _exposure(host_rows: list[dict[str, Any]],
              canonical_wire_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Measure what each current consumer advertises, without changing policy."""
    host_names = {row["name"] for row in host_rows}
    canonical_wire = {row["name"]: row for row in canonical_wire_rows}
    from sico.adapters.tool_profiles import host_scope
    from sico.codex.profile_setting import PRODUCT_DEFAULT

    compat_scope = host_scope("desktop_compat", host_rows)
    supported_names = set(compat_scope["tools"])
    desktop_scope = host_scope(PRODUCT_DEFAULT, host_rows)
    desktop_names = set(desktop_scope["tools"])
    desktop_rows = [row for row in host_rows if row["name"] in desktop_names]
    entry: dict[str, Any] = {
        "sico_desktop": {
            "consumer": "SiCo project service (CodexRuntime)",
            "source": "sico/python/sico/codex/runtime_assembly.py",
            "filter": (f"session tool profile snapshot; default {PRODUCT_DEFAULT} "
                       "(enabled_tools and tools/list share one snapshot)"),
            "profile": PRODUCT_DEFAULT,
            "host_unsupported": sorted(host_names - supported_names),
            "outside_default": sorted(supported_names - desktop_names),
            "measurement": measurement(desktop_rows),
        },
        "managed_agent": {},
    }
    for profile in hosts.managed_agent_profiles():
        enabled = hosts.managed_agent_exposure(profile)
        selected = [canonical_wire[name] for name in enabled if name in canonical_wire]
        entry["managed_agent"][profile] = {
            "consumer": "cadai managed agent (AI Assistant)",
            "source": "cadai/python/cadai/codex_launch.py",
            "filter": f"enabled_tools = allowed_tool_names({profile!r})",
            "enabled": len(enabled),
            "missing_from_source": sorted(name for name in enabled
                                          if name not in canonical_wire),
            "missing_from_host": sorted(name for name in enabled
                                        if name not in host_names),
            "measurement": measurement(selected),
        }
    entry["host_only"] = sorted(host_names - set(canonical_wire))
    entry["canonical_missing_in_host"] = sorted(set(canonical_wire) - host_names)
    return entry


def _task_groups(host_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Resolve the runtime declarations against the assembled host catalog."""
    hosts.ensure_source_paths()
    from cadai.task_group_resolver import resolve

    declarations = hosts.task_group_declarations()
    rows_by_name = {row["name"]: row for row in host_rows}
    entries, union, membership = [], {}, {}
    for group_id in declarations:
        resolved = resolve(group_id, declarations=declarations, catalog=rows_by_name)
        entries.append({
            "id": group_id, "owner": resolved["owner"], "status": resolved["status"],
            "purpose": resolved["purpose"], "keywords": resolved["keywords"],
            "subgroups": resolved["subgroups"],
            "tool_count": resolved["tool_count"],
            "conditional_count": resolved["conditional_count"],
            "bytes": resolved["bytes"], "digest": resolved["digest"],
            "available": resolved["available"],
            "missing": [row["name"] for row in resolved["missing"]],
            "conditional_missing": [row["name"] for row in resolved["conditional_missing"]],
            "recovery": resolved["recovery"],
        })
        for name in (*resolved["tools"], *resolved["conditional"]):
            union.setdefault(name, rows_by_name[name])
        for name in resolved["tools"]:
            membership.setdefault(name, []).append(group_id)
    return {
        "contract": GROUPS_CONTRACT,
        "source": "cadai.task_groups + sico.adapters.task_groups (runtime declarations)",
        "groups": entries,
        "union": measurement(list(union.values())),
        "shared_members": {name: sorted(owners) for name, owners in sorted(membership.items())
                           if len(owners) > 1},
    }


def _profiles(host_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Resolve the runtime tool-profile declarations against the host catalog."""
    hosts.ensure_source_paths()
    from sico.adapters.tool_profiles import PROFILES, host_scope

    entries = []
    for profile_id, declaration in PROFILES.items():
        scope = host_scope(profile_id, host_rows)
        entries.append({
            "id": profile_id,
            "owner": declaration["owner"],
            "status": declaration["status"],
            "purpose": declaration["purpose"],
            "scope": declaration["scope"],
            "groups": list(declaration["groups"]),
            "defaults": list(declaration["defaults"]),
            "tool_count": len(scope["tools"]),
            "bytes": scope["bytes"],
            "conditional_count": len(scope["conditional"]),
            "recovery_count": len(scope["recovery"]),
            "operations": list(scope["operations"]),
            "digest": scope["digest"],
            "missing": [row["name"] for row in scope["missing"]],
            "unavailable": [row["name"] for row in scope["missing"]
                            if row.get("state") != "unregistered"],
        })
    return {
        "contract": PROFILES_CONTRACT,
        "source": "sico.adapters.tool_profiles (runtime declarations)",
        "profiles": entries,
    }


def build() -> dict[str, Any]:
    """Assemble the whole report; deterministic for one source commit."""
    canonical = hosts.canonical_definitions()
    canonical_wire = hosts.canonical_wire_rows()
    host = hosts.host_rows()
    return {
        "contract": CONTRACT,
        "method": METHOD,
        "scope": (
            "offline source assembly; canonical definitions, canonical MCP fields, "
            "SiCo host projection and current exposures. Not a live session request "
            "and not a model token measurement."
        ),
        "source": {
            "root": str(hosts.ROOT),
            "commit": _git(hosts.ROOT, "rev-parse", "HEAD"),
            "dirty": bool(_git(hosts.ROOT, "status", "--porcelain", "--", "tools")),
            "python": sys.version.split()[0],
            "modules": {
                "canonical": hosts.canonical_module(),
                "host": hosts.host_module(),
            },
        },
        "wire_fields": list(WIRE_FIELDS),
        "layers": {
            "canonical": _layer(
                "definitions", "cadai.mcp_definitions.TOOLS", canonical),
            "canonical_wire": _layer(
                "wire", "cadai.mcp_definitions.TOOLS projected to MCP fields",
                canonical_wire),
            "host": _layer(
                "wire", "sico context_tools + workbench via ToolBridge",
                host),
        },
        "exposure": _exposure(host, canonical_wire),
        "task_groups": _task_groups(host),
        "tool_profiles": _profiles(host),
    }


def diff(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Layer-level added/removed/renamed/changed diff between two reports."""
    result: dict[str, Any] = {"from": old.get("source", {}), "to": new.get("source", {})}
    layers = {}
    for name in sorted(set(old.get("layers", {})) | set(new.get("layers", {}))):
        before = old.get("layers", {}).get(name)
        after = new.get("layers", {}).get(name)
        if before is None or after is None:
            layers[name] = {"present": [before is not None, after is not None]}
            continue
        entry = compare(before["measurement"]["tools"], after["measurement"]["tools"])
        entry["count"] = [before["measurement"]["count"], after["measurement"]["count"]]
        entry["array_compact_bytes"] = [
            before["measurement"]["array_compact_bytes"],
            after["measurement"]["array_compact_bytes"],
        ]
        layers[name] = entry
    result["layers"] = layers
    for section, fields in (("task_groups", "groups"), ("tool_profiles", "profiles")):
        before = {row["id"]: row for row in old.get(section, {}).get(fields, [])}
        after = {row["id"]: row for row in new.get(section, {}).get(fields, [])}
        delta = {}
        for entry_id in sorted(set(before) | set(after)):
            old_row, new_row = before.get(entry_id), after.get(entry_id)
            if old_row is None or new_row is None:
                delta[entry_id] = {"present": [old_row is not None, new_row is not None]}
                continue
            delta[entry_id] = {
                "tool_count": [old_row["tool_count"], new_row["tool_count"]],
                "bytes": [old_row["bytes"], new_row["bytes"]],
                "digest": [old_row["digest"], new_row["digest"]],
                "missing": [old_row.get("missing", []), new_row.get("missing", [])],
            }
        result[section] = delta
    return result


def _summary(report: dict[str, Any]) -> str:
    lines = []
    for name, layer in report["layers"].items():
        measured = layer["measurement"]
        lines.append(
            f"{name:>14}: {measured['count']:>4} tools, "
            f"array {measured['array_compact_bytes']} B compact, "
            f"schema annotations {measured['schema_annotation_bytes']} B, "
            f"sha256 {measured['sha256'][:12]}")
    d = report["exposure"]["sico_desktop"]["measurement"]
    lines.append(f"{'desktop expo':>14}: {d['count']} tools, "
                 f"{d['array_compact_bytes']} B compact")
    for profile, entry in report["exposure"]["managed_agent"].items():
        m = entry["measurement"]
        lines.append(f"{'agent ' + profile:>14}: {m['count']} tools, "
                     f"{m['array_compact_bytes']} B compact")
    for entry in report["task_groups"]["groups"]:
        flag = " draft" if entry["status"] == "draft" else ""
        if entry["missing"] or entry["conditional_missing"]:
            flag += " incomplete"
        lines.append(
            f"{'group ' + entry['id']:>14}: {entry['tool_count']} tools / "
            f"{entry['bytes']} B, conditional {entry['conditional_count']}{flag}")
    union = report["task_groups"]["union"]
    lines.append(f"{'group union':>14}: {union['count']} unique tools, "
                 f"{union['array_compact_bytes']} B compact")
    for entry in report.get("tool_profiles", {}).get("profiles", []):
        flag = " draft" if entry["status"] == "draft" else ""
        if entry["missing"]:
            flag += f" missing={len(entry['missing'])}"
        lines.append(
            f"{'profile ' + entry['id']:>14}: {entry['tool_count']} tools / "
            f"{entry['bytes']} B{flag}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path,
                        help="write the report JSON here; default prints to stdout")
    parser.add_argument("--compare", type=Path,
                        help="diff against an earlier report JSON")
    arguments = parser.parse_args(argv)

    report = build()
    if arguments.compare is not None:
        old = json.loads(arguments.compare.read_text(encoding="utf-8"))
        print(json.dumps(diff(old, report), ensure_ascii=False, indent=2))
        return 0
    if arguments.output is not None:
        arguments.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(_summary(report))
        print(f"report written to {arguments.output}")
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
