"""Controller-owned paths, process budget and source checks for LDO gates."""

from __future__ import annotations

import os
from pathlib import Path
import time
from typing import Any, Mapping

from ..design_ir.assembler_io import payload_artifact, read_json
from ..errors import EnvironmentError
from ..executor import ExecutorContext, ExecutorResult
from ..ldo_qualification import build_source_snapshot
from ..process import ProcessResult, run_process_group
from ..workspace import sha256_file, write_json_once


AUTHORITY = "controller-owned-virtuoso-read-only"
_CHILD_ENV = frozenset({
    "PATH", "LD_LIBRARY_PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL",
    "DISPLAY", "XAUTHORITY", "TMPDIR", "TMP", "TEMP", "SHELL", "TZ",
    "CDSHOME", "CDS_ROOT", "CDS_AUTO_64BIT", "CDS_Netlisting_Mode",
    "CDS_LIC_FILE", "LM_LICENSE_FILE", "CDS_LIC_QUEUE", "AMSHOME",
    "SPECTRE_HOME", "SPECTRE_DEFAULTS", "MMSIMHOME", "XCELIUM_HOME",
    "UVM_HOME", "OA_HOME", "CDS_INST_DIR", "CDS_SITE",
})


def project_inputs(context: ExecutorContext) -> tuple[Mapping[str, Any], str]:
    project = context.metadata.get("project_config")
    if not isinstance(project, Mapping) or project.get("library") != "amsLDO":
        raise ValueError("LDO structure adapter needs a profile-owned amsLDO mapping")
    target = context.recipe.target
    cell = target.get("cell")
    if target.get("library") != "amsLDO" or cell not in ("LDO_MASTER", "LDO_AON"):
        raise ValueError("LDO structure adapter target is unsupported")
    if target.get("source_views") != {
        "schematic": "schematic", "symbol": "symbol", "config": "config"
    }:
        raise ValueError("LDO structure adapter requires schematic, symbol and config views")
    return project, str(cell)


def source_snapshot(context: ExecutorContext, *, authentication=None) -> dict[str, Any]:
    project, cell = project_inputs(context)
    return build_source_snapshot(
        str(project["root"]), library="amsLDO", cells=(cell,), scope="recipe_target",
        cds_lib=project["cds_lib"], source_cds_lib=project["source_cds_lib"],
        mapping_root=context.metadata["workspace_root"],
        mapping_allowed_roots=project["mapping_allowed_roots"],
        mapping_environment=project.get("mapping_environment", {}),
        model_root=project["model_root"],
        required_library_mappings=tuple(project["required_library_mappings"]),
        required_model_sections=tuple(project["required_model_sections"]),
        authenticated_snapshot=authentication,
    )


def require_source(value: Mapping[str, Any], *, generation: str | None = None) -> str:
    actual = value.get("source_generation")
    if value.get("errors") or not isinstance(actual, str) or len(actual) != 64:
        raise ValueError("LDO source preflight is incomplete: " + str(value.get("errors", [])))
    if generation is not None and actual != generation:
        raise ValueError("source_generation changed during the read-only recipe run")
    return actual


def child_environment(context: ExecutorContext, cds_lib: Path) -> dict[str, str]:
    result = {key: value for key, value in context.environment.items() if key in _CHILD_ENV}
    result.update({"CDS_LIB": str(cds_lib), "CDS_CDSLIB": str(cds_lib),
                   "PWD": str(context.gate_root)})
    return result


def remaining(deadline: float) -> float:
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise TimeoutError("LDO structure gate exhausted its process budget")
    return seconds


def run_phase(context: ExecutorContext, tool: str, arguments, *, environment, log: Path,
              deadline: float) -> ProcessResult:
    path = context.tools.get(tool)
    if not path or not Path(path).is_absolute() or not os.access(path, os.X_OK):
        raise EnvironmentError(f"qualified {tool} executable is unavailable")
    result = run_process_group(
        (path, *map(str, arguments)), cwd=context.gate_root,
        environment=environment, log_file=log, timeout=remaining(deadline),
    )
    write_json_once(log.with_suffix(".process.json"), result.to_dict())
    if result.timed_out:
        raise TimeoutError(f"{tool} timed out")
    if result.returncode != 0:
        raise ValueError(f"{tool} exited with {result.returncode}")
    return result


def indexed_dependency(context: ExecutorContext, dependency: ExecutorResult,
                       name: str, digest_name: str) -> Path:
    path = payload_artifact(context.run.payload_root, str(dependency.outputs.get(name, "")))
    if path not in dependency.artifacts or sha256_file(path) != dependency.outputs.get(digest_name):
        raise ValueError(f"snapshot {name} is unindexed or hash-mismatched")
    return path


def artifacts(context: ExecutorContext) -> tuple[Path, ...]:
    return tuple(sorted(path for path in context.gate_root.rglob("*") if path.is_file() and not path.is_symlink()))


def blocked(context: ExecutorContext, exc: Exception) -> ExecutorResult:
    if isinstance(exc, TimeoutError):
        status = "BLOCKED_TIMEOUT"
    elif isinstance(exc, EnvironmentError):
        status = "BLOCKED_ENVIRONMENT"
    elif "source_generation changed" in str(exc):
        status = "STALE_SOURCE"
    else:
        status = "BLOCKED_INPUT"
    report = {"status": status, "reason": str(exc), "scope": "read_only_structure"}
    write_json_once(context.gate_root / "blocked-evidence.json", report)
    return ExecutorResult(status, report, artifacts=artifacts(context))


def read_inspection(path: Path, *, kind: str, cell: str | None = None) -> dict[str, Any]:
    value = read_json(path)
    if value.get("ok") is not True or value.get("truncated") is not False:
        raise ValueError(f"{path.name} did not provide complete read-only evidence")
    if value.get("kind") != kind:
        raise ValueError(f"{path.name} inspection kind mismatch")
    if cell is not None and (value.get("modified") is not False or value.get("cellview") != {
        "lib": "amsLDO", "cell": cell, "view": kind,
    }):
        raise ValueError(f"{path.name} saved-state or identity mismatch")
    return value
