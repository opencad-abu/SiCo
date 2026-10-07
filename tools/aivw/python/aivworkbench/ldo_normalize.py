"""Normalize live LDO HED, dbAccess and runams evidence offline.

The normalizer is deliberately file based.  It never starts a Cadence tool;
the live producer owns process execution and places raw evidence in a split
payload before this module is called.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Mapping

from .errors import WorkspaceError
from .m0s_normalize import _instance_bindings, _parse_bindings
from .workspace import sha256_file, stable_digest
from .workspace import write_json_once
from .design_ir.official_ams import build_config_binding_from_official_structure


_TOP = re.compile(r'config designtop="(?P<library>[^.]+)\.(?P<cell>[^:]+):(?P<view>[^"]+)"')
_IE = re.compile(
    r"\bie\s+vsup=(?P<vsup>[^\s]+)(?:\s+connrules=(?P<rule>[^\s]+))?"
    r"\s+discipline=(?P<discipline>[A-Za-z0-9_.$+-]+)", re.IGNORECASE
)
_BOUND_CELL = re.compile(
    r'^\s*"(?P<cell>[^"]+)"\s+\("(?P<library>[^"]+)"\s+"(?P<view>[^"]+)"\)'
)
_DEFINE = re.compile(r"^\s*DEFINE\s+(?P<name>[A-Za-z_][A-Za-z0-9_$.-]*)\s+\S+\s*$")
_REPLIST = re.compile(r'\brepList\s+"([^"]+)"')
_STOPLIST = re.compile(r'\bstopList\s+"([^"]+)"')


def _json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WorkspaceError(f"{label} evidence is not valid JSON: {path}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise WorkspaceError(f"{label} evidence has an unsupported schema: {path}")
    return value


def _artifact(root: Path, path: Path, role: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise WorkspaceError(f"required LDO artifact is unavailable: {path}")
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise WorkspaceError(f"LDO artifact escaped payload: {path}")
    return {
        "path": resolved.relative_to(root).as_posix(),
        "sha256": sha256_file(resolved),
        "size": resolved.stat().st_size,
        "media_type": "text/x-verilog-ams" if "connect_rule" in role else "application/octet-stream",
    }


def _connect_rule_evidence(netlist: Path, payload_root: Path) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    ie_path = netlist / "ie_card.scs"
    text = ie_path.read_text(encoding="utf-8", errors="replace") if ie_path.is_file() else ""
    match = _IE.search(text)
    args_path = netlist / "xrunArgs"
    args = args_path.read_text(encoding="utf-8", errors="replace") if args_path.is_file() else ""
    arg_rule = re.search(r"(?m)^\s*-amsconnrules\s+([A-Za-z0-9_.$:+-]+)\s*$", args)
    arg_discipline = re.search(r"(?m)^\s*-discipline\s+([A-Za-z0-9_.$+-]+)\s*$", args)
    rule = match.group("rule") if match else (arg_rule.group(1) if arg_rule else None)
    discipline = match.group("discipline") if match else (arg_discipline.group(1) if arg_discipline else None)
    domain = {"logic": "digital", "electrical": "analog", "wreal": "wreal"}.get(discipline)
    if not discipline or not domain:
        return [], []
    disciplines = [{"net": "*", "discipline": discipline, "domain": domain}]
    if not rule:
        # A bare IE card asks AMS Designer to synthesize UCM internally.  It
        # is useful evidence, but it is not an auditable project rule file.
        return [], disciplines
    rule_name = rule.split(".")[-1].split(":")[-1]
    # runams does not copy built-in rule source into the netlist.  The live
    # producer stages the approved source at ``connect-rules/<name>.vams``.
    candidates = sorted(payload_root.glob(f"connect-rules/{rule_name}.*"))
    candidates = [item for item in candidates if item.is_file() and not item.is_symlink()]
    if not candidates:
        return [], disciplines
    source = candidates[0]
    source_text = source.read_text(encoding="utf-8", errors="replace")
    if not re.search(r"\bconnectrules\b", source_text, re.IGNORECASE):
        return [], disciplines
    return [
        {
            "name": rule_name,
            "artifact": _artifact(payload_root, source, "official_connect_rule"),
            "sha256": sha256_file(source),
        }
    ], disciplines


def _bound_cell_libraries(netlist: Path) -> dict[str, tuple[str, str]]:
    """Read the runams alias table used when ``.binding.cfg`` omits ``lib``."""
    table = netlist / ".bindedCellToLVTable"
    if not table.is_file():
        return {}
    result: dict[str, tuple[str, str]] = {}
    for line in table.read_text(encoding="utf-8", errors="replace").splitlines():
        match = _BOUND_CELL.match(line)
        if match:
            result[match.group("cell")] = (match.group("library"), match.group("view"))
    return result


def _run_defaults(payload_root: Path, netlist: Path) -> dict[str, list[str]]:
    """Read defaults emitted by the run-local mapping and AMS map files."""
    liblist: list[str] = []
    cds = payload_root / "cds.lib"
    if cds.is_file():
        for line in cds.read_text(encoding="utf-8", errors="replace").splitlines():
            match = _DEFINE.match(line)
            if match:
                liblist.append(match.group("name"))
    map_text = ""
    for candidate in (netlist / "digital" / "map" / "current", netlist / "analog" / "map" / "current"):
        if candidate.is_file():
            map_text += candidate.read_text(encoding="utf-8", errors="replace")
    view_match = _REPLIST.search(map_text)
    stop_match = _STOPLIST.search(map_text)
    viewlist = view_match.group(1).split() if view_match else []
    stoplist = stop_match.group(1).split() if stop_match else []
    return {
        "liblist": sorted(set(liblist)),
        "viewlist": viewlist,
        "stoplist": stoplist,
    }


def normalize_ldo_structure(
    *,
    cell: str,
    hed_path: Path,
    catalog_path: Path,
    runams_root: Path,
    payload_root: Path,
    runams_log: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return bounded normalized evidence and adapter-ready gate evidence."""

    hed = _json(hed_path, f"HED {cell}")
    catalog = _json(catalog_path, "dbAccess catalog")
    netlist = runams_root / "netlist"
    required = {
        "binding": netlist / ".binding.cfg",
        "instance_binding": netlist / ".instBindInfoTable",
        "ports": netlist / ".dbTermInfo",
        "ams_netlist": netlist / "netlist.vams",
        "global_mapping": netlist / "digital" / "ihnl" / "globalmap",
        "bus_name_mapping": netlist / "digital" / "map" / "current",
        "netlist_status": netlist / ".netlistStatus",
    }
    for name in ("xrunArgs", "ie_card.scs"):
        if (netlist / name).is_file():
            required[name] = netlist / name
    missing = [name for name, path in required.items() if not path.is_file() or path.stat().st_size == 0]
    top, bindings = _parse_bindings(required["binding"]) if not missing else ({}, [])
    aliases = _bound_cell_libraries(netlist)
    for row in bindings:
        if not row.get("library"):
            library_view = aliases.get(row["cell"])
            if library_view:
                row["library"] = library_view[0]
    instance_bindings = _instance_bindings(required["instance_binding"]) if not missing else []
    log_text = runams_log.read_text(encoding="utf-8", errors="replace") if runams_log.is_file() else ""
    status = required["netlist_status"].read_text(encoding="utf-8", errors="replace").strip() if required["netlist_status"].is_file() else ""
    cells = {str(item.get("name")): item for item in catalog.get("cells", []) if isinstance(item, Mapping)}
    catalog_cell = cells.get(cell, {})
    schematic = catalog_cell.get("schematic") if isinstance(catalog_cell, Mapping) else {}
    connect_rules, discipline = _connect_rule_evidence(netlist, payload_root)
    defaults = _run_defaults(payload_root, netlist)
    source_map = {
        name: _artifact(payload_root, path, "official_raw_evidence")
        for name, path in required.items()
        if path.is_file()
    }
    normalized: dict[str, Any] = {
        "schema_version": 1,
        "identity": {"library": "amsLDO", "cell": cell, "config": "config"},
        "top": top,
        "defaults": {
            "liblist": defaults["liblist"],
            "viewlist": defaults["viewlist"],
            "stoplist": defaults["stoplist"],
        },
        "hed": {"provider": hed.get("provider"), "config_opened": hed.get("config_opened"), "top": hed.get("top"), "traversal": hed.get("traversal")},
        "oa_catalog": {"provider": catalog.get("provider"), "authoritative": catalog.get("authoritative"), "top_opened": isinstance(schematic, Mapping) and schematic.get("opened") is True, "cell": cell, "terminal_count": schematic.get("terminal_count") if isinstance(schematic, Mapping) else None, "instance_count": schematic.get("instance_count") if isinstance(schematic, Mapping) else None},
        "ams_unl": {"provider": "runams/AMS Unified Netlister", "bindings": bindings, "instance_bindings": instance_bindings, "sources": source_map, "connect_rule_count": len(connect_rules)},
        "discipline_bindings": discipline,
        "normalized_sha256": "",
        "evidence": {},
    }
    normalized["evidence"] = {
        "top_matches": top == hed.get("top") and top.get("library") == "amsLDO" and top.get("cell") == cell,
        "hed_traversal_ok": hed.get("config_opened") is True and isinstance(hed.get("traversal"), Mapping) and hed["traversal"].get("status") == "OK",
        "catalog_authoritative": catalog.get("provider") == "dbAccess" and catalog.get("authoritative") is True and catalog.get("read_only") is True,
        "catalog_top_opened": isinstance(schematic, Mapping) and schematic.get("opened") is True,
        "explicit_instance_bindings_accounted": bool(instance_bindings),
        "success_marker": bool(re.search(r"completed successfully|successfully completed", log_text, re.IGNORECASE)),
        "fatal_log_marker": bool(re.search(r"(?:\*F,|\*ERROR\*|ERROR \((?:AMS|RUNAMS|OSSHNL)-)", log_text, re.IGNORECASE)),
        "unresolved_count": 0 if (netlist / "digital" / ".cellTblWithoutBind").is_file() and not (netlist / "digital" / ".cellTblWithoutBind").read_text(errors="replace").strip() else None,
        "netlist_status": status,
        "missing_artifacts": missing,
        "connect_rule_evidence": bool(connect_rules),
    }
    normalized["normalized_sha256"] = stable_digest({key: value for key, value in normalized.items() if key not in {"normalized_sha256", "evidence"}})
    return normalized, normalized["evidence"]


def build_official_ldo_binding(
    *,
    cell: str,
    payload_root: Path,
    source_generation: str,
    source_preflight: Mapping[str, Any],
    target: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build and publish one official LDO config binding from payload evidence.

    This is an offline step.  It refuses to run while the original source
    mapping is blocked, preserving the distinction between a successful
    run-local staged mapping and an authenticated source qualification.
    """
    payload = payload_root.resolve(strict=True)
    if source_preflight.get("status") != "PASS":
        raise WorkspaceError("official LDO binding requires PASS source preflight")
    normalized_path = payload / f"normalized-{cell}.json"
    normalized = _json(normalized_path, f"normalized {cell}")
    evidence = normalized.get("evidence")
    if not isinstance(evidence, Mapping):
        raise WorkspaceError(f"normalized {cell} has no evidence object")
    runams_root = payload / "runams" / cell
    rules, disciplines = _connect_rule_evidence(runams_root / "netlist", payload)
    binding = build_config_binding_from_official_structure(
        normalized,
        target=target or {"library": "amsLDO", "cell": cell, "config_view": "config"},
        source_generation=source_generation,
        payload_root=payload,
        source_root=payload,
        evidence=evidence,
        discipline_bindings=disciplines,
        connect_rules=rules,
    )
    output = payload / f"config-binding-{cell}.json"
    write_json_once(output, binding.to_dict())
    return {
        "path": output.relative_to(payload).as_posix(),
        "sha256": sha256_file(output),
        "size": output.stat().st_size,
        "cell": cell,
        "source_generation": source_generation,
    }


__all__ = ["build_official_ldo_binding", "normalize_ldo_structure"]
