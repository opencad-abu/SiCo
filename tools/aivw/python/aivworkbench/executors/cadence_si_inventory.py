"""Cadence SI artifact inventory and result policy."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

from ..workspace import sha256_file
from ..executor import ExecutorResult

def _inventory(
    run_dir: Path,
    payload_root: Path,
    *,
    library: str,
    cell: str,
    view: str,
) -> dict[str, Any]:
    def valid(path: Path) -> bool:
        try:
            return path.is_file() and not path.is_symlink() and path.stat().st_size > 0
        except OSError:
            return False

    netlists = sorted(
        path for path in (run_dir / "ihnl").glob("*/netlist") if valid(path)
    )
    map_path = run_dir / "map" / "current"
    globalmap_path = run_dir / "ihnl" / "globalmap"
    inherited_path = run_dir / ".InheritConnInfo"
    module_map_paths = sorted(
        path for path in (run_dir / "ihnl").glob("*/map") if valid(path)
    )
    control_paths = sorted(
        path for path in (run_dir / "ihnl").glob("*/control") if valid(path)
    )
    relative_netlists = [path.relative_to(payload_root).as_posix() for path in netlists]
    run_relative_netlists = [path.relative_to(run_dir).as_posix() for path in netlists]
    relative_map = (
        map_path.relative_to(payload_root).as_posix() if valid(map_path) else None
    )
    relative_globalmap = (
        globalmap_path.relative_to(payload_root).as_posix()
        if valid(globalmap_path)
        else None
    )
    target_present = False
    if relative_globalmap:
        target_pattern = re.compile(
            rf"(?:^|\n){re.escape(library)}/{re.escape(cell)}/{re.escape(view)}(?:\s|$)"
        )
        target_present = bool(
            target_pattern.search(
                globalmap_path.read_text(encoding="utf-8", errors="replace")
            )
        )
    artifact_paths = [*netlists]
    if relative_map:
        artifact_paths.append(map_path)
    if relative_globalmap:
        artifact_paths.append(globalmap_path)
    if valid(inherited_path):
        artifact_paths.append(inherited_path)
    artifact_paths.extend(module_map_paths)
    artifact_paths.extend(control_paths)
    return {
        "schema_version": 1,
        "root": run_dir.relative_to(payload_root).as_posix(),
        "netlist": relative_netlists,
        "map": relative_map,
        "globalmap": relative_globalmap,
        "inherited_connections": inherited_path.relative_to(payload_root).as_posix()
        if valid(inherited_path)
        else None,
        "module_maps": [
            path.relative_to(payload_root).as_posix() for path in module_map_paths
        ],
        "controls": [path.relative_to(payload_root).as_posix() for path in control_paths],
        "run_relative": {
            "netlist": run_relative_netlists,
            "map": map_path.relative_to(run_dir).as_posix() if relative_map else None,
            "globalmap": globalmap_path.relative_to(run_dir).as_posix()
            if relative_globalmap
            else None,
        },
        "records": [_artifact_record(path, payload_root) for path in artifact_paths],
        "target": {"library": library, "cell": cell, "view": view},
        "target_present_in_globalmap": target_present,
        "artifact_paths": [
            str(path.relative_to(payload_root)) for path in artifact_paths
        ],
        "complete": bool(
            netlists and relative_map and relative_globalmap and target_present
        ),
    }

def _artifact_record(path: Path, payload_root: Path) -> dict[str, object]:
    return {
        "path": path.relative_to(payload_root).as_posix(),
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
    }

def _pass(
    process: Mapping[str, Any],
    success_evidence: bool,
    errors: list[str],
    inventory: Mapping[str, Any],
    si_env_unchanged: bool,
    cds_lib_unchanged: bool,
) -> bool:
    return (
        process.get("returncode") == 0
        and not process.get("timed_out")
        and success_evidence
        and not errors
        and si_env_unchanged
        and cds_lib_unchanged
        and bool(inventory.get("complete"))
    )

def _result(status: str, detail: str, code: str) -> ExecutorResult:
    return ExecutorResult(status, {"code": code, "detail": detail})
