"""Normalize M0-S HED, OA, and AMS UNL structural evidence."""

from __future__ import annotations

import json
from pathlib import Path
import re

from .errors import EnvironmentError
from .workspace import stable_digest


_TABLE_ROW = re.compile(
    r"^(?P<library>\S+)\s+(?P<cell>\S+)\s+(?P<view>\S+)\s*(?P<note>\*Stopping View\*)?\s*$"
)
_BINDING = re.compile(
    r'^\s*config cell="(?P<cell>[^"]+)"(?:\s+lib="(?P<library>[^"]+)")?\s+view="(?P<view>[^"]+)"\s*$'
)
_DESIGN_TOP = re.compile(r'^\s*config designtop="(?P<library>[^.]+)\.(?P<cell>[^:]+):(?P<view>[^"]+)"')
_MODULE = re.compile(r"^module\s+(?P<name>\\\S+|[A-Za-z_$][\w$]*)\s*\((?P<ports>.*?)\);", re.MULTILINE | re.DOTALL)
_INSTANCE_LINE = re.compile(r'^\s*"(?P<instance>[^"]+)"\s+(?P<body>\(.*\))\s*$')
_CELLVIEW_TUPLE = re.compile(
    r'\("(?P<library>[^"]+)"\s+"(?P<cell>[^"]+)"\s+'
    r'"(?P<view>[^"]+)"\s+"[^"]+"\)'
)
_FATAL = re.compile(r"(?:\*F,|\*ERROR\*|ERROR \((?:AMS|RUNAMS|OSSHNL)-)", re.IGNORECASE)


def _load_json(path: Path, label: str) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EnvironmentError(f"invalid {label} worker output {path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise EnvironmentError(f"unsupported {label} worker output: {path}")
    return payload


def _parse_table(log_text: str) -> list[dict[str, object]]:
    start = "Beginning netlist configuration information"
    end = "End of netlist configuration information"
    if start not in log_text or end not in log_text:
        return []
    body = log_text.split(start, 1)[1].split(end, 1)[0]
    result: list[dict[str, object]] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith(("-", "LIB NAME")):
            continue
        match = _TABLE_ROW.fullmatch(line)
        if match:
            item: dict[str, object] = match.groupdict()
            item["stopping"] = bool(item.pop("note"))
            result.append(item)
    return sorted(result, key=lambda item: (str(item["library"]), str(item["cell"])))


def _parse_bindings(path: Path) -> tuple[dict[str, str], list[dict[str, str]]]:
    top: dict[str, str] = {}
    bindings: list[dict[str, str]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = _DESIGN_TOP.match(line)
        if match:
            top = match.groupdict()
        match = _BINDING.match(line)
        if match:
            row = match.groupdict()
            # AMS UNL omits ``lib`` for cells resolved from the active config
            # liblist.  Keep that evidence explicit instead of dropping it.
            row["library"] = row.get("library") or ""
            bindings.append(row)
    return top, sorted(bindings, key=lambda item: (item["library"], item["cell"]))


def _module_summary(path: Path) -> list[dict[str, object]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    result: list[dict[str, object]] = []
    for match in _MODULE.finditer(text):
        ports = [item.strip() for item in match.group("ports").replace("\n", " ").split(",")]
        result.append({"name": match.group("name").strip(), "port_count": len([p for p in ports if p])})
    return result


def _instance_bindings(path: Path) -> list[dict[str, str]]:
    values: set[tuple[tuple[str, str], ...]] = set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = _INSTANCE_LINE.match(line)
        if not match:
            continue
        tuples = [item.groupdict() for item in _CELLVIEW_TUPLE.finditer(match.group("body"))]
        # Each candidate binding is represented by a bound cell followed by
        # its parent cell.  A line may contain several candidate pairs for
        # repeated PCells or hierarchy branches; preserve every pair.
        for index in range(0, len(tuples) - 1, 2):
            bound = tuples[index]
            parent = tuples[index + 1]
            row = {
                "parent_library": parent["library"],
                "parent_cell": parent["cell"],
                "parent_view": parent["view"],
                "instance": match.group("instance"),
                "bound_library": bound["library"],
                "bound_cell": bound["cell"],
                "bound_view": bound["view"],
            }
            values.add(tuple(sorted(row.items())))
    return sorted(
        (dict(value) for value in values),
        key=lambda item: (
            item["parent_library"], item["parent_cell"], item["parent_view"],
            item["instance"], item["bound_library"], item["bound_cell"], item["bound_view"],
        ),
    )


def _evidence_file(path: Path, netlist_root: Path) -> dict[str, object]:
    return {
        "path": path.relative_to(netlist_root).as_posix(),
        "role": "official_raw_evidence",
    }


def _normalized_top_schematic(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {"opened": False, "terminals": [], "instances": []}
    terminals = sorted(
        value.get("terminals", []),
        key=lambda item: (str(item.get("name")), str(item.get("direction"))),
    )
    instances: list[dict[str, object]] = []
    for raw in value.get("instances", []):
        item = dict(raw)
        item["connections"] = sorted(
            item.get("connections", []),
            key=lambda entry: (str(entry.get("terminal")), str(entry.get("net"))),
        )
        instances.append(item)
    instances.sort(key=lambda item: str(item.get("name")))
    return {"opened": value.get("opened") is True, "terminals": terminals, "instances": instances}


def normalize_structure(
    *,
    hed_path: Path,
    catalog_path: Path,
    config: dict[str, object],
    runams_log: Path,
    netlist_root: Path,
) -> tuple[dict[str, object], dict[str, object]]:
    hed = _load_json(hed_path, "HED")
    catalog = _load_json(catalog_path, "catalog")
    log_text = runams_log.read_text(encoding="utf-8", errors="replace")
    required = {
        "binding": netlist_root / ".binding.cfg",
        "instance_binding": netlist_root / ".instBindInfoTable",
        "ports": netlist_root / ".dbTermInfo",
        "ams_netlist": netlist_root / "netlist.vams",
        "global_mapping": netlist_root / "digital" / "ihnl" / "globalmap",
        "bus_name_mapping": netlist_root / "digital" / "map" / "current",
        "netlist_status": netlist_root / ".netlistStatus",
    }
    missing = [name for name, path in required.items() if not path.is_file() or path.stat().st_size == 0]
    top, bindings = _parse_bindings(required["binding"]) if not missing else ({}, [])
    instance_bindings = (
        _instance_bindings(required["instance_binding"])
        if required["instance_binding"].is_file()
        else []
    )
    cellviews = _parse_table(log_text)
    unresolved_file = netlist_root / "digital" / ".cellTblWithoutBind"
    unresolved = 0 if unresolved_file.is_file() and not unresolved_file.read_text().strip() else 1
    top_schematic = _normalized_top_schematic(catalog.get("top_schematic", {}))
    source_map = {name: _evidence_file(path, netlist_root) for name, path in required.items() if path.is_file()}
    canonical: dict[str, object] = {
        "schema_version": 1,
        "identity": {"library": "zambezi45_sim", "cell": "pll_sim", "config": "config_function"},
        "top": top,
        "defaults": {
            "liblist": config["liblist"],
            "viewlist": config["viewlist"],
            "stoplist": config["stoplist"],
        },
        "explicit_rules": {
            "cell": config["cell_bindings"],
            "instance": config["instance_bindings"],
        },
        "hed": {
            "provider": hed.get("provider"),
            "config_opened": hed.get("config_opened"),
            "top": hed.get("top"),
            "traversal": hed.get("traversal"),
        },
        "oa_catalog": {
            "provider": catalog.get("provider"),
            "authoritative": catalog.get("authoritative"),
            "libraries": [
                {"name": item.get("name"), "read_path": item.get("read_path"), "cell_count": len(item.get("cells", []))}
                for item in catalog.get("libraries", [])  # type: ignore[union-attr]
            ],
            "top_schematic": top_schematic,
        },
        "ams_unl": {
            "provider": "runams/AMS Unified Netlister",
            "cellviews": cellviews,
            "bindings": bindings,
            "instance_bindings": instance_bindings,
            "modules": _module_summary(required["ams_netlist"]) if required["ams_netlist"].is_file() else [],
            "sources": source_map,
        },
        "connectivity_provenance": {
            "hierarchy": ["HED traversal", ".instBindInfoTable"],
            "ports": ["dbAccess top_schematic", ".dbTermInfo", "netlist.vams"],
            "instance_terminal_net": ["dbAccess top_schematic", "netlist.vams", "amap"],
            "global": ["digital/ihnl/globalmap", "allGlobals"],
            "bus_and_name_mapping": ["digital/map/current", "amap"],
        },
    }
    top_expected = config["top"]
    top_ok = top == top_expected and hed.get("top") == top_expected
    status_text = required["netlist_status"].read_text().strip() if required["netlist_status"].is_file() else ""
    expected_cells = {
        (item["lib"], item["cell"], item["view"])
        for item in config["cell_bindings"]  # type: ignore[union-attr]
    }
    actual_cells = {(item["library"], item["cell"], item["view"]) for item in bindings}
    traversed_cellviews = {
        (item["library"], item["cell"], item["view"]) for item in cellviews
    }
    active_explicit_instances: list[dict[str, str]] = []
    inactive_explicit_instances: list[dict[str, str]] = []
    missing_explicit_instances: list[dict[str, str]] = []
    for item in config["instance_bindings"]:  # type: ignore[union-attr]
        parent = (item["parent_lib"], item["parent_cell"], item["parent_view"])
        observed = any(
            binding["instance"] == item["instance"]
            and (
                binding["parent_library"],
                binding["parent_cell"],
                binding["parent_view"],
            ) == parent
            for binding in instance_bindings
        )
        if observed:
            active_explicit_instances.append(item)
        elif parent not in traversed_cellviews:
            inactive_explicit_instances.append(item)
        else:
            missing_explicit_instances.append(item)
    evidence: dict[str, object] = {
        "top_matches": top_ok,
        "hed_traversal_ok": hed.get("config_opened") is True and hed.get("traversal", {}).get("status") == "OK",  # type: ignore[union-attr]
        "catalog_authoritative": catalog.get("provider") == "dbAccess" and catalog.get("authoritative") is True,
        "catalog_top_opened": isinstance(top_schematic, dict) and top_schematic.get("opened") is True,
        "cellview_count": len(cellviews),
        "stopping_cellview_count": sum(bool(item["stopping"]) for item in cellviews),
        "binding_count": len(bindings),
        "instance_binding_count": len(instance_bindings),
        "explicit_cell_bindings_preserved": expected_cells <= actual_cells,
        "active_explicit_instance_bindings": active_explicit_instances,
        "inactive_explicit_instance_bindings": inactive_explicit_instances,
        "missing_explicit_instance_bindings": missing_explicit_instances,
        "explicit_instance_bindings_accounted": not missing_explicit_instances,
        "unresolved_count": unresolved,
        "missing_artifacts": missing,
        "fatal_log_marker": bool(_FATAL.search(log_text)),
        "success_marker": "AMS UNL netlisting has completed successfully" in log_text,
        "netlist_status": status_text,
    }
    canonical["normalized_sha256"] = stable_digest(canonical)
    return canonical, evidence
