"""Bounded summaries for LDO catalog, HED and AMS netlisting evidence."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Mapping

from .ldo_live_contract import LDO_CELLS, LDO_PORTS
from .ldo_qualification_values import READ_ONLY_AUTHORITIES
from .m0s_normalize import _instance_bindings, _parse_bindings
from .process import ProcessResult


BINDING_ANY = re.compile(
    r'^\s*config cell="(?P<cell>[^"]+)"(?:\s+lib="(?P<library>[^"]+)")?\s+view="(?P<view>[^"]+)"'
)
FATAL = re.compile(r"(?:\*F,|\*ERROR\*|ERROR \((?:AMS|RUNAMS|OSSHNL)-)", re.IGNORECASE)


def catalog_summary(catalog: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    findings: list[str] = []
    if (
        catalog.get("provider") != "dbAccess"
        or catalog.get("authoritative") is not True
    ):
        findings.append("dbAccess catalog is not authoritative")
    if catalog.get("read_only") is not True:
        findings.append("dbAccess catalog did not prove read_only=true")
    if catalog.get("library") != "amsLDO":
        findings.append("catalog library does not match amsLDO")
    cells = catalog.get("cells")
    rows: list[dict[str, Any]] = []
    if not isinstance(cells, list):
        findings.append("catalog cells are missing")
        cells = []
    for cell in cells:
        if not isinstance(cell, Mapping):
            findings.append("catalog cell row is malformed")
            continue
        name = str(cell.get("name", ""))
        schematic = cell.get("schematic")
        views = cell.get("views")
        if name not in LDO_CELLS:
            findings.append("catalog contains an unexpected cell")
            continue
        if not isinstance(views, list) or not {
            "schematic",
            "symbol",
            "config",
            "verilogams",
        }.issubset(set(views)):
            findings.append(f"{name} does not expose all required views")
        if not isinstance(schematic, Mapping) or schematic.get("opened") is not True:
            findings.append(f"{name} schematic was not opened read-only")
            continue
        terminals = schematic.get("terminals")
        names = (
            tuple(
                str(item.get("name")) for item in terminals if isinstance(item, Mapping)
            )
            if isinstance(terminals, list)
            else ()
        )
        if names != LDO_PORTS[name] and set(names) != set(LDO_PORTS[name]):
            findings.append(f"{name} terminal contract does not match source")
        rows.append(
            {
                "cell": name,
                "view_count": len(views) if isinstance(views, list) else 0,
                "terminal_count": schematic.get("terminal_count"),
                "instance_count": schematic.get("instance_count"),
                "port_names": sorted(names),
                "opened": True,
            }
        )
    if {row["cell"] for row in rows} != set(LDO_CELLS):
        findings.append("catalog did not return both LDO topologies")
    return {
        "library": catalog.get("library"),
        "cells": sorted(rows, key=lambda row: row["cell"]),
    }, sorted(set(findings))


def hed_summary(hed: Mapping[str, Any], cell: str) -> tuple[dict[str, Any], list[str]]:
    findings: list[str] = []
    if (
        hed.get("provider") != "Virtuoso/HED"
        or hed.get("authority") not in READ_ONLY_AUTHORITIES
    ):
        findings.append(f"{cell} HED authority is not the read-only worker")
    if hed.get("config_opened") is not True:
        findings.append(f"{cell} config was not opened")
    config = hed.get("config")
    top = hed.get("top")
    if (
        not isinstance(config, Mapping)
        or config.get("library") != "amsLDO"
        or config.get("cell") != cell
        or config.get("view") != "config"
    ):
        findings.append(f"{cell} config identity does not match target")
    if (
        not isinstance(top, Mapping)
        or top.get("library") != "amsLDO"
        or top.get("cell") != cell
        or top.get("view") != "schematic"
    ):
        findings.append(f"{cell} HED top identity does not match target")
    traversal = hed.get("traversal")
    traversal_ok = isinstance(traversal, Mapping) and traversal.get("status") == "OK"
    if not traversal_ok:
        findings.append(f"{cell} HED traversal did not complete")
    if hed.get("formal_saved_state") is not True:
        findings.append(f"{cell} HED did not prove formal saved state")
    if hed.get("double_read_equal") is not True:
        findings.append(f"{cell} HED double read is not equal")
    return {
        "cell": cell,
        "config_opened": hed.get("config_opened") is True,
        "top": dict(top) if isinstance(top, Mapping) else None,
        "traversal_ok": traversal_ok,
        "authenticated": hed.get("authority") in READ_ONLY_AUTHORITIES
        and hed.get("formal_saved_state") is True
        and hed.get("double_read_equal") is True,
        "source_generation": hed.get("source_generation"),
    }, sorted(set(findings))


def runams_summary(
    root: Path,
    log_file: Path,
    result: ProcessResult,
    *,
    payload_root: Path | None = None,
) -> dict[str, Any]:
    files = {
        path.relative_to(root).as_posix(): path
        for path in root.rglob("*")
        if path.is_file()
    }
    status_path = files.get("netlist/.netlistStatus") or files.get(".netlistStatus")
    status_text = (
        status_path.read_text(encoding="utf-8", errors="replace").strip()
        if status_path
        else ""
    )
    binding_path = next(
        (
            path
            for rel, path in files.items()
            if rel.endswith("/.binding.cfg") or rel == ".binding.cfg"
        ),
        None,
    )
    instance_binding_path = next(
        (
            path
            for rel, path in files.items()
            if rel.endswith("/.instBindInfoTable") or rel == ".instBindInfoTable"
        ),
        None,
    )
    binding_count = 0
    top: dict[str, str] = {}
    bindings: list[dict[str, str]] = []
    if binding_path:
        top, bindings = _parse_bindings(binding_path)
        binding_count = sum(
            1
            for line in binding_path.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()
            if BINDING_ANY.match(line)
        )
    log_text = (
        log_file.read_text(encoding="utf-8", errors="replace")
        if log_file.is_file()
        else ""
    )
    unresolved_path = next(
        (path for rel, path in files.items() if rel.endswith("/.cellTblWithoutBind")),
        None,
    )
    unresolved_count = (
        0
        if unresolved_path
        and not unresolved_path.read_text(encoding="utf-8", errors="replace").strip()
        else (1 if unresolved_path else None)
    )
    connect_rule_files = {
        rel: path
        for rel, path in files.items()
        if "connrules" in rel.casefold()
        or rel.casefold().endswith("crules.vams")
        or rel.casefold().endswith(".crules")
    }
    if payload_root is not None:
        for path in sorted((payload_root / "connect-rules").glob("*")):
            if path.is_file() and not path.is_symlink():
                connect_rule_files.setdefault(
                    path.relative_to(payload_root).as_posix(), path
                )
    connect_rule_evidence = bool(
        connect_rule_files
        and any(
            re.search(
                r"\bconnectrules\b",
                path.read_text(encoding="utf-8", errors="replace"),
                re.IGNORECASE,
            )
            for path in connect_rule_files.values()
        )
    )
    return {
        "returncode": result.returncode,
        "timed_out": result.timed_out,
        "netlist_status": status_text or None,
        "success_marker": bool(
            re.search(
                r"completed successfully|successfully completed",
                log_text,
                re.IGNORECASE,
            )
        ),
        "fatal_log_marker": bool(FATAL.search(log_text)),
        "top": top,
        "binding_count": binding_count,
        "cell_bindings": bindings if binding_path else [],
        "instance_binding_count": len(_instance_bindings(instance_binding_path))
        if instance_binding_path
        else 0,
        "unresolved_count": unresolved_count,
        "connect_rule_evidence": connect_rule_evidence,
        "connect_rule_file_count": len(connect_rule_files),
        "artifact_count": len(files),
        "required_artifacts": {
            name: any(rel.endswith(suffix) for rel in files)
            for name, suffix in {
                "binding": ".binding.cfg",
                "instance_binding": ".instBindInfoTable",
                "ports": ".dbTermInfo",
                "ams_netlist": "netlist.vams",
                "netlist_status": ".netlistStatus",
            }.items()
        },
    }


__all__ = ["catalog_summary", "hed_summary", "runams_summary"]
