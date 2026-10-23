"""Declarative budget gate for the MCP tool surface (M4).

Run from the repository root; imports source only, touches no EDA, session or
model:

    /software/pkgs/python/3.9.13/bin/python3 tools/utility/mcp_catalog_budget.py

The checker builds the same offline report as ``mcp_catalog_report`` and
compares it against ``tools/utility/mcp_catalog_budget.json``:

- byte ceilings stay frozen; every membership change needs an exact ledger;
- every added/removed/renamed/changed tool row must be listed in the budget's
  change ledger with a reason and owner, so no change hides behind a refresh;
- per-tool description budgets and full-size class limits are enforced; a tool
  over a hard limit must be a registered debt whose ceiling only shrinks;
- profile budgets are enforced the same way (over-budget only as registered
  debt, and frozen debts must not grow);
- task groups and profiles must resolve without unexplained missing members;
- aliases are never advertised and never duplicate a canonical name;
- host-unsupported tools are never advertised on the compat surface.

The checker never rewrites the budget file. ``--freeze PATH`` writes a proposal;
the base comparison still rejects policy resets and increased ceilings.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import mcp_catalog_report as report_module
from mcp_catalog_history import check_history, ledger_binding, profile_contract_errors
from a_philosophy_sources import base_json

BUDGET_FORMAT = "sico-mcp-budget.v1"
LAYERS = ("canonical", "host")

# Section-6 candidate targets, frozen with the rest of this budget.
PROFILE_TARGETS = {
    "desktop_compat": 204800, "desktop_core": 40960, "circuit": 131072,
    "simulation": 98304, "pdk_maintenance": 98304, "verification": 32768,
    "physical_flow": 65536, "mixed_design": 163840, "adaptive": 196608,
}


def load_budget(path: Path) -> dict[str, Any]:
    budget = json.loads(path.read_text(encoding="utf-8"))
    if budget.get("format") != BUDGET_FORMAT:
        raise ValueError(f"unsupported budget format: {budget.get('format')!r}")
    return budget


def _classify(structural: int, limits: dict[str, Any]) -> str:
    if structural <= limits["simple"]["structural_max"]:
        return "simple"
    if structural <= limits["composite"]["structural_max"]:
        return "composite"
    return "structure"


def _inventory(measurement: dict[str, Any]) -> dict[str, str]:
    return {row["name"]: row["content_sha256"]
            for row in measurement["tools"]}


def _covers(entry: dict[str, Any], layer: str, kind: str, name: str) -> bool:
    if entry.get("layer") != layer:
        return False
    if entry.get("kind") == kind and entry.get("name") == name:
        return True
    if kind in {"added", "removed"} and entry.get("kind") == "renamed":
        return entry.get({"added": "to", "removed": "from"}[kind]) == name
    return False


def _layer_checks(report: dict[str, Any], budget: dict[str, Any],
                  ledger: list[dict[str, Any]], errors: list[str]) -> None:
    used: set[int] = set()
    for layer, frozen in budget["layers"].items():
        measurement = report["layers"][layer]["measurement"]
        if frozen["count"] != len(frozen["tools"]):
            errors.append(f"{layer}: frozen count disagrees with its inventory")
        if measurement["array_compact_bytes"] > frozen["array_compact_bytes"]:
            errors.append(
                f"{layer}: array bytes {measurement['array_compact_bytes']} grow beyond "
                f"frozen {frozen['array_compact_bytes']}")
        old, new = frozen["tools"], _inventory(measurement)
        for name in sorted(set(old) | set(new)):
            if old.get(name) == new.get(name):
                continue
            kind = ("added" if name not in old else
                    "removed" if name not in new else "changed")
            matches = [index for index, entry in enumerate(ledger)
                       if _covers(entry, layer, kind, name)
                       and ledger_binding(entry, old, new)]
            if matches:
                used.update(matches)
            elif not any(_covers(entry, layer, kind, name)
                         for entry in budget.get("removals", [])):
                errors.append(
                    f"{layer}: unexplained {kind} tool {name}; add a change-ledger "
                    "entry or a reviewed removal record in the same commit")
    for index, entry in enumerate(ledger):
        if index not in used:
            errors.append(
                f"stale change-ledger entry {entry.get('kind')} "
                f"{entry.get('name') or entry.get('from') or entry.get('to')} "
                f"on {entry.get('layer')}: nothing changed; remove it")
    for entry in budget.get("removals", []):
        label = entry.get("name") or entry.get("from")
        for field in ("kind", "layer", "owner", "reason"):
            if not isinstance(entry.get(field), str) or not entry[field].strip():
                errors.append(
                    f"removal record {label}: {field} must be reviewed and non-empty")
        if isinstance(entry.get("layer"), str) and entry["layer"] in budget["layers"]:
            names = budget["layers"][entry["layer"]]["tools"]
            if entry.get("kind") == "renamed":
                if entry.get("from") in names or entry.get("to") not in names:
                    errors.append(
                        f"stale rename record {entry.get('from')} -> {entry.get('to')}")
            elif entry.get("kind") == "removed" and entry.get("name") in names:
                errors.append(f"stale removal record {entry.get('name')}: still present")


def _tool_checks(report: dict[str, Any], budget: dict[str, Any],
                 errors: list[str], warnings: list[str]) -> None:
    debts = {entry["tool"]: entry for entry in budget["debt"]["tools"]}
    seen: set[str] = set()
    rows = report["layers"].get("canonical_wire", report["layers"]["canonical"])
    for row in rows["measurement"]["tools"]:
        name = row["name"]
        structural = row.get("structural_bytes")
        if structural is None:
            errors.append(f"{name}: schema structure was not measured")
            continue
        kind = _classify(structural, budget["class_limits"])
        limits = budget["class_limits"][kind]
        budget_desc = budget["description_budget"][kind]
        debt = debts.get(name)
        if debt is not None:
            seen.add(name)
            if debt["class"] != kind:
                errors.append(
                    f"{name}: debt class {debt['class']} != measured {kind}")
            if row["compact_bytes"] <= debt["limit"]:
                errors.append(
                    f"{name}: registered debt no longer exceeds its limit "
                    f"{debt['limit']} B; remove it from the budget in the same commit")
            elif row["compact_bytes"] > debt["bytes"]:
                errors.append(
                    f"{name}: registered debt grows {row['compact_bytes']} > "
                    f"{debt['bytes']} (owner {debt['owner']})")
        elif row["compact_bytes"] > limits["hard"]:
            errors.append(
                f"{name}: {row['compact_bytes']} B exceeds the {kind} hard limit "
                f"{limits['hard']} B; register a debt with owner/exit condition")
        elif row["compact_bytes"] > limits["warn"]:
            warnings.append(
                f"{name}: {row['compact_bytes']} B exceeds the {kind} warn limit "
                f"{limits['warn']} B")
        # description_bytes includes the JSON quotes; the budget is the text.
        if row["description_bytes"] - 2 > budget_desc:
            errors.append(
                f"{name}: description {row['description_bytes'] - 2} B exceeds the "
                f"{kind} budget {budget_desc} B")
    for name, debt in debts.items():
        if name not in seen:
            errors.append(
                f"{name}: registered debt no longer exceeds its limit; remove it "
                "from the budget in the same commit")


def _profile_checks(report: dict[str, Any], budget: dict[str, Any],
                    errors: list[str], warnings: list[str]) -> None:
    current = {row["id"]: row for row in report["tool_profiles"]["profiles"]}
    for profile_id, frozen in budget["profiles"].items():
        row = current.get(profile_id)
        if row is None:
            errors.append(f"profile {profile_id}: missing from the report")
            continue
        if (profile_id not in budget.get("profile_contracts", {})
                and row["tool_count"] != frozen["count"]):
            errors.append(
                f"profile {profile_id}: {row['tool_count']} tools != frozen "
                f"{frozen['count']}")
        if frozen["status"] == "debt":
            if row["bytes"] > frozen["bytes"]:
                errors.append(
                    f"profile {profile_id}: {row['bytes']} B grows beyond the frozen "
                    f"debt {frozen['bytes']} B (owner {frozen['owner']})")
        elif row["bytes"] > frozen["budget"]:
            errors.append(
                f"profile {profile_id}: {row['bytes']} B exceeds budget "
                f"{frozen['budget']} B; register a debt with owner/exit condition")
        allowed = budget.get("allowed_missing", {}).get("profiles", {}) \
            .get(profile_id, [])
        # Host-unsupported members are recorded facts (allowed_missing);
        # unreachable registered members are the error this check owns.
        unexpected = sorted(set(row["missing"]) - set(row.get("unavailable", []))
                            - set(allowed))
        if unexpected:
            errors.append(
                f"profile {profile_id}: unresolved members {unexpected}")
    for profile_id in current:
        if profile_id not in budget["profiles"]:
            errors.append(f"profile {profile_id}: absent from the budget")


def _group_checks(report: dict[str, Any], budget: dict[str, Any],
                  errors: list[str]) -> None:
    allowed = budget.get("allowed_missing", {}).get("groups", {})
    for row in report["task_groups"]["groups"]:
        declared = [*row["missing"], *row["conditional_missing"]]
        unexpected = sorted(set(declared) - set(allowed.get(row["id"], [])))
        if unexpected:
            errors.append(f"group {row['id']}: unresolved members {unexpected}")


def _alias_checks(report: dict[str, Any], errors: list[str]) -> None:
    import mcp_catalog_hosts as hosts

    hosts.ensure_source_paths()
    from cadai.mcp_definitions import ALIASES, TOOLS
    from cadai.mcp_registry import validate_aliases

    validate_aliases({row["name"]: row for row in TOOLS}, ALIASES)
    for layer in LAYERS:
        names = [row["name"] for row in report["layers"][layer]["measurement"]["tools"]]
        if len(names) != len(set(names)):
            errors.append(f"{layer}: duplicate advertised tool names")
        advertised = set(names) & set(ALIASES)
        if advertised:
            errors.append(f"{layer}: aliases advertised: {sorted(advertised)}")


def _support_checks(report: dict[str, Any], budget: dict[str, Any],
                    errors: list[str], warnings: list[str]) -> None:
    desktop = report["exposure"]["sico_desktop"]
    unsupported = set(desktop.get("host_unsupported", []))
    frozen = set(budget["host_unsupported"])
    if unsupported != frozen:
        warnings.append(
            "host-unsupported set changed: "
            f"added {sorted(unsupported - frozen)}, removed {sorted(frozen - unsupported)}")
    advertised = {row["name"] for row in desktop["measurement"]["tools"]}
    leaked = sorted(advertised & unsupported)
    if leaked:
        errors.append(f"compat surface advertises host-unsupported tools: {leaked}")


def check(report: dict[str, Any], budget: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    ledger = list(budget.get("changes", []))
    for index, entry in enumerate(ledger):
        for field in ("kind", "layer", "reason", "owner"):
            if not isinstance(entry.get(field), str) or not entry[field].strip():
                errors.append(f"change-ledger entry {index}: {field} must be non-empty text")
        if entry.get("kind") in {"added", "removed", "changed"} and not entry.get("name"):
            errors.append(f"change-ledger entry {index}: name is required")
        if entry.get("kind") == "renamed" and not (entry.get("from") and entry.get("to")):
            errors.append(f"change-ledger entry {index}: renamed needs from/to")
    _layer_checks(report, budget, ledger, errors)
    _tool_checks(report, budget, errors, warnings)
    _profile_checks(report, budget, errors, warnings)
    _group_checks(report, budget, errors)
    _alias_checks(report, errors)
    _support_checks(report, budget, errors, warnings)
    errors.extend(profile_contract_errors(report, budget))
    return {"passed": not errors, "errors": errors, "warnings": warnings}


def _removals(report: dict[str, Any],
              previous: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Carry reviewed removal/rename records and open new ones for review.

    A freeze can absorb inventory edits, but a vanished tool must still state
    why and for whom: new records start with an empty reason so the same
    commit has to fill it in.  Content hashes ignore the tool name, so a pure
    rename is detected as one reviewed pair.
    """
    if previous is None:
        return []
    records = [dict(entry) for entry in previous.get("removals", [])]
    pending = {entry.get("name") or entry.get("from"): entry
               for entry in previous.get("changes", [])}
    for layer in LAYERS:
        old = previous["layers"][layer]["tools"]
        new = _inventory(report["layers"][layer]["measurement"])
        added_by_hash: dict[str, list[str]] = {}
        for name, digest in new.items():
            added_by_hash.setdefault(digest, []).append(name)
        renamed_to: set[str] = set()
        for name in sorted(set(old) - set(new)):
            candidates = [target for target in added_by_hash.get(old[name], [])
                          if target not in old]
            if len(candidates) == 1:
                renamed = {"kind": "renamed", "layer": layer, "from": name,
                           "to": candidates[0], "owner": "sico", "reason": ""}
                for key in (name, candidates[0]):
                    source = pending.get(key)
                    if source and source.get("kind") in {"renamed", "removed", "added"}:
                        renamed["reason"] = source.get("reason", "")
                        renamed["owner"] = source.get("owner", renamed["owner"])
                records.append(renamed)
                renamed_to.add(candidates[0])
                continue
            record = {"kind": "removed", "layer": layer, "name": name,
                      "owner": "sico", "reason": ""}
            source = pending.get(name)
            if source:
                record["reason"] = source.get("reason", "")
                record["owner"] = source.get("owner", record["owner"])
            records.append(record)
        for target in renamed_to:
            records = [entry for entry in records
                       if not (entry.get("kind") == "removed" and entry.get("name") == target)]
    # Drop records that no longer describe the frozen state.
    kept = []
    for entry in records:
        layer_names = (report["layers"][entry["layer"]]["measurement"]["tools"]
                       if entry.get("layer") in report["layers"] else [])
        names = {row["name"] for row in layer_names}
        if entry.get("kind") == "renamed":
            if entry.get("from") in names or entry.get("to") not in names:
                continue
        elif entry.get("name") in names:
            continue
        kept.append(entry)
    return kept


def freeze(report: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a snapshot proposal; this is not a policy-change authorization."""
    tool_layer = report["layers"].get("canonical_wire", report["layers"]["canonical"])
    tools = tool_layer["measurement"]["tools"]
    limits = {
        "simple": {"structural_max": 1024, "warn": 2048, "hard": 4096},
        "composite": {"structural_max": 4096, "warn": 4096, "hard": 8192},
        "structure": {"structural_max": None, "warn": 8192, "hard": 12288},
    }
    debts: list[dict[str, Any]] = []
    owner = {entry["tool"]: entry
             for entry in (previous or {}).get("debt", {}).get("tools", [])}
    for row in tools:
        kind = _classify(row["structural_bytes"], limits)
        if row["compact_bytes"] > limits[kind]["hard"]:
            old = owner.get(row["name"], {})
            debts.append({
                "tool": row["name"], "class": kind, "bytes": row["compact_bytes"],
                "limit": limits[kind]["hard"], "severity": "hard",
                "owner": old.get("owner", "sico"),
                "exit": old.get("exit", "结构治理后重估（M6 资格前）"),
                "reason": old.get(
                    "reason",
                    "电路/模板结构输入的展开体积超过类别硬上限；"
                    "需结构治理或预算重估，未削减工具能力"),
            })
    profiles = {}
    for row in report["tool_profiles"]["profiles"]:
        old = (previous or {}).get("profiles", {}).get(row["id"], {})
        target = old.get("budget", PROFILE_TARGETS.get(row["id"], row["bytes"]))
        status = "debt" if row["bytes"] > target else "ok"
        profiles[row["id"]] = {
            "count": row["tool_count"], "bytes": row["bytes"],
            "budget": target,
            "status": status,
            "owner": old.get("owner", "sico"),
            "exit": old.get("exit", "目录收敛后重估（M6 默认切换前）" if status == "debt" else ""),
        }
    return {
        "format": BUDGET_FORMAT,
        "frozen_at": report["source"]["commit"],
        "method": report["method"],
        "description_budget": {"simple": 512, "composite": 768, "structure": 1024},
        "class_limits": limits,
        "layers": {
            layer: {
                "count": report["layers"][layer]["measurement"]["count"],
                "array_compact_bytes":
                    report["layers"][layer]["measurement"]["array_compact_bytes"],
                "sha256": report["layers"][layer]["measurement"]["sha256"],
                "tools": _inventory(report["layers"][layer]["measurement"]),
            }
            for layer in LAYERS
        },
        "profiles": profiles,
        "debt": {"tools": debts},
        "host_unsupported": sorted(report["exposure"]["sico_desktop"].get("host_unsupported", [])),
        "changes": [],
        "removals": _removals(report, previous),
        "allowed_missing": {
            "groups": {},
            "profiles": {row["id"]: sorted(row["unavailable"])
                         for row in report["tool_profiles"]["profiles"]
                         if row["unavailable"]},
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-ref", default="HEAD", help="Committed policy to compare; resets fail")
    parser.add_argument("--budget", type=Path,
                        default=Path(__file__).with_name("mcp_catalog_budget.json"))
    parser.add_argument("--report", type=Path,
                        help="check an existing report JSON instead of rebuilding")
    parser.add_argument("--freeze", type=Path,
                        help="write a snapshot proposal; does not authorize a policy reset")
    arguments = parser.parse_args(argv)

    if arguments.report is not None:
        report = json.loads(arguments.report.read_text(encoding="utf-8"))
    else:
        report = report_module.build()
    if arguments.freeze is not None:
        previous = None
        if arguments.budget.is_file():
            previous = load_budget(arguments.budget)
        arguments.freeze.write_text(
            json.dumps(freeze(report, previous), ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8")
        print(f"snapshot proposal written to {arguments.freeze}; this does not authorize a policy reset")
        return 0

    budget = load_budget(arguments.budget)
    result = check(report, budget)
    root = Path(__file__).resolve().parents[2]
    result["errors"].extend(check_history(
        budget, base_json(root, arguments.base_ref, arguments.budget.resolve())))
    result["passed"] = not result["errors"]
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
