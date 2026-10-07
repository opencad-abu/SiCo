"""Cadence SystemVerilog Integration (``si``) structure executor."""

from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import re
import time
from typing import Mapping

from ..executor import ExecutorContext, ExecutorResult
from ..workspace import sha256_file, write_json_once
from .cadence_si_input import _compare_si_env, _render_si_env
from .cadence_si_environment import _project_environment, _run_process
from .cadence_si_inventory import _inventory, _pass, _result

_HASH = re.compile(r"^[0-9a-f]{64}$")
_OA_NAME = re.compile(r"^[A-Za-z0-9_$+.-]+$")
_DIAGNOSTIC = re.compile(r"\b(?:VLOGNET|OSSHNL)-[0-9]+\b")
_ERROR_DIAGNOSTIC = re.compile(r"(?:^\s*(?:ERROR|FATAL)\b|\b(?:ERROR|FATAL)\s*\((?:VLOGNET|OSSHNL)-[0-9]+\)|\*E,|\*F,)", re.IGNORECASE)
_SUCCESS = re.compile(r"(?:netlisting\s+succeeded|end\s+(?:incremental\s+)?netlisting)", re.IGNORECASE)

def run_cadence_si(context: ExecutorContext) -> ExecutorResult:
    deadline = time.monotonic() + context.timeout
    structure = context.recipe.payload.get("structure")
    required = (
        set(structure.get("required_artifacts", ()))
        if isinstance(structure, Mapping)
        else set()
    )
    supported_required = {"netlist", "map", "globalmap", "netlister_log"}
    if not isinstance(structure, Mapping) or structure.get("provider") != "cadence_si":
        return _result(
            "BLOCKED_INPUT",
            "cadence.si executor requires structure.provider=cadence_si",
            "structure_provider_mismatch",
        )
    if required != supported_required:
        return _result(
            "BLOCKED_INPUT",
            "cadence_si required_artifacts contract is incomplete or unsupported",
            "structure_artifact_contract_mismatch",
        )
    snapshot = context.dependencies.get("snapshot")
    if snapshot is None or snapshot.status != "PASS":
        return _result(
            "BLOCKED_INPUT", "snapshot dependency did not PASS", "snapshot_dependency"
        )
    source_generation = snapshot.outputs.get("source_generation")
    if not isinstance(source_generation, str) or not _HASH.fullmatch(source_generation):
        return _result(
            "BLOCKED_INPUT",
            "snapshot dependency has no valid source_generation",
            "missing_source_generation",
        )

    target = context.recipe.target
    library = str(target.get("library", ""))
    cell = str(target.get("cell", ""))
    views = target.get("source_views")
    if not isinstance(views, Mapping):
        return _result(
            "BLOCKED_INPUT",
            "recipe source_views is not an object",
            "invalid_source_views",
        )
    source_view = views.get("config") or views.get("schematic")
    if not all(
        isinstance(value, str) and _OA_NAME.fullmatch(value)
        for value in (library, cell, source_view)
    ):
        return _result(
            "BLOCKED_INPUT",
            "recipe target identity is invalid",
            "invalid_target_identity",
        )

    raw_cds_lib = context.metadata.get("cds_lib")
    if not isinstance(raw_cds_lib, str) or not raw_cds_lib:
        return _result(
            "BLOCKED_INPUT",
            "profile has no approved cds.lib mapping for the target library",
            "cds_lib_unconfigured",
        )
    cds_lib = Path(raw_cds_lib).expanduser()
    if not cds_lib.is_absolute():
        return _result(
            "BLOCKED_INPUT", "approved cds.lib must be absolute", "cds_lib_not_absolute"
        )
    cds_lib = cds_lib.resolve(strict=False)
    if "smic28" in str(cds_lib).casefold():
        return _result(
            "BLOCKED_INPUT",
            "approved cds.lib is in the excluded smic28 workspace",
            "forbidden_workspace",
        )
    raw_workspace_root = context.metadata.get("workspace_root")
    if (
        not isinstance(raw_workspace_root, str)
        or not Path(raw_workspace_root).is_absolute()
    ):
        return _result(
            "BLOCKED_INPUT",
            "profile has no absolute approved workspace root",
            "workspace_root_unavailable",
        )
    workspace_root = Path(raw_workspace_root).resolve(strict=False)
    if not cds_lib.is_relative_to(workspace_root):
        return _result(
            "BLOCKED_INPUT",
            "approved cds.lib escaped the profile workspace root",
            "cds_lib_path_escape",
        )
    if cds_lib.is_symlink() or not cds_lib.is_file():
        return _result(
            "BLOCKED_INPUT",
            "approved cds.lib is not a regular file",
            "cds_lib_unavailable",
        )
    cds_lib_sha256_before = sha256_file(cds_lib)
    try:
        project_environment = _project_environment(context.metadata, workspace_root)
    except ValueError as exc:
        return _result(
            "BLOCKED_INPUT",
            str(exc),
            "invalid_project_environment",
        )

    si = context.tools.get("si", "")
    if not si:
        return _result(
            "BLOCKED_ENVIRONMENT",
            "qualified si executable is unavailable",
            "si_unavailable",
        )
    si_path = Path(si)
    if (
        not si_path.is_absolute()
        or not si_path.is_file()
        or not os.access(si_path, os.X_OK)
    ):
        return _result(
            "BLOCKED_ENVIRONMENT",
            "qualified si executable is not executable",
            "si_unavailable",
        )

    root = context.gate_root
    run_dir = root / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    input_dir = root / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    si_env = input_dir / "si.env"
    run_si_env = run_dir / "si.env"
    try:
        si_env_text = _render_si_env(library, cell, source_view)
        si_env.write_text(si_env_text, encoding="utf-8")
        run_si_env.write_text(si_env_text, encoding="utf-8")
    except OSError as exc:
        return _result(
            "BLOCKED_ENVIRONMENT", f"cannot write si.env: {exc}", "si_env_write_failed"
        )

    log = root / "si.log"
    command = (str(si_path), "-batch", "-command", "netlist", "-cdslib", str(cds_lib))
    environment = dict(context.environment)
    for name in tuple(environment):
        if name.startswith(("SICO_AI_", "CAD_AI_", "CAD_CODEX_")):
            environment.pop(name, None)
    environment.update(project_environment)
    process_timeout = deadline - time.monotonic()
    if process_timeout <= 0:
        return ExecutorResult(
            "BLOCKED_TIMEOUT",
            {"code": "si_timeout", "detail": "gate budget expired before si started"},
            {"structure_authoritative": False, "source_generation": source_generation},
            (si_env, run_si_env),
        )
    process, output = _run_process(
        command,
        cwd=run_dir,
        environment=environment,
        timeout=process_timeout,
    )
    try:
        log.write_text(output, encoding="utf-8")
    except OSError as exc:
        return _result(
            "BLOCKED_ENVIRONMENT", f"cannot write si log: {exc}", "si_log_write_failed"
        )

    diagnostics = [
        line.strip() for line in output.splitlines() if _DIAGNOSTIC.search(line)
    ]
    error_diagnostics = [
        line.strip() for line in output.splitlines() if _ERROR_DIAGNOSTIC.search(line)
    ]
    success_evidence = bool(_SUCCESS.search(output))
    si_env_identity_unchanged, si_env_writeback = _compare_si_env(
        si_env,
        run_si_env,
        library=library,
        cell=cell,
        view=source_view,
    )
    si_env_runtime_writeback_allowed = bool(
        si_env_writeback["valid"] and si_env_writeback["unexpected_keys"] == []
    )
    si_env_unchanged = (
        run_si_env.is_file()
        and not run_si_env.is_symlink()
        and sha256_file(run_si_env) == sha256_file(si_env)
    )
    cds_lib_sha256_after = (
        sha256_file(cds_lib) if cds_lib.is_file() and not cds_lib.is_symlink() else None
    )
    cds_lib_unchanged = cds_lib_sha256_after == cds_lib_sha256_before
    inventory = _inventory(
        run_dir,
        context.run.payload_root,
        library=library,
        cell=cell,
        view=source_view,
    )
    inventory_path = root / "structure-inventory.json"
    evidence_path = root / "si-evidence.json"
    write_json_once(inventory_path, inventory)
    evidence = {
        "schema_version": 1,
        "provider": "cadence_si",
        "status": "PASS"
        if _pass(
            process,
            success_evidence,
            error_diagnostics,
            inventory,
            si_env_identity_unchanged and si_env_runtime_writeback_allowed,
            cds_lib_unchanged,
        )
        else "NOT_PASS",
        "command": list(command),
        "cwd": str(run_dir),
        "cds_lib": str(cds_lib),
        "cds_lib_sha256_before": cds_lib_sha256_before,
        "cds_lib_sha256_after": cds_lib_sha256_after,
        "cds_lib_unchanged": cds_lib_unchanged,
        "project": context.metadata.get("project"),
        "project_environment": project_environment,
        "si_env": str(si_env.relative_to(root)),
        "run_si_env": str(run_si_env.relative_to(root)),
        "si_env_sha256": sha256_file(si_env),
        "run_si_env_sha256": sha256_file(run_si_env) if run_si_env.is_file() else None,
        "si_env_unchanged": si_env_unchanged,
        "si_env_identity_unchanged": si_env_identity_unchanged,
        "si_env_runtime_writeback_allowed": si_env_runtime_writeback_allowed,
        "si_env_writeback": si_env_writeback,
        "source_generation": source_generation,
        "process": process,
        "success_evidence": success_evidence,
        "diagnostics": diagnostics,
        "error_diagnostics": error_diagnostics,
        "inventory_sha256": sha256_file(inventory_path),
    }
    write_json_once(evidence_path, evidence)

    artifacts = tuple(
        path
        for path in (
            si_env,
            run_si_env,
            log,
            inventory_path,
            evidence_path,
            *(context.run.payload_root / path for path in inventory["artifact_paths"]),
        )
        if path.is_file() and not path.is_symlink()
    )
    if process.get("timed_out"):
        return ExecutorResult(
            "BLOCKED_TIMEOUT",
            {"code": "si_timeout", "evidence": evidence},
            {"structure_authoritative": False, "source_generation": source_generation},
            artifacts,
        )
    if process.get("returncode") is None or process.get("returncode") != 0:
        return ExecutorResult(
            "FAIL_CADENCE_SI",
            {"code": "si_process_failed", "evidence": evidence},
            {"structure_authoritative": False, "source_generation": source_generation},
            artifacts,
        )
    if error_diagnostics:
        return ExecutorResult(
            "FAIL_CADENCE_SI",
            {"code": "si_error_diagnostics", "evidence": evidence},
            {"structure_authoritative": False, "source_generation": source_generation},
            artifacts,
        )
    if not success_evidence:
        return ExecutorResult(
            "FAIL_CADENCE_SI",
            {"code": "si_success_evidence_missing", "evidence": evidence},
            {"structure_authoritative": False, "source_generation": source_generation},
            artifacts,
        )
    if not si_env_identity_unchanged:
        return ExecutorResult(
            "FAIL_CADENCE_SI",
            {"code": "si_env_identity_changed", "evidence": evidence},
            {"structure_authoritative": False, "source_generation": source_generation},
            artifacts,
        )
    if not si_env_runtime_writeback_allowed:
        return ExecutorResult(
            "FAIL_CADENCE_SI",
            {"code": "si_env_writeback_not_allowed", "evidence": evidence},
            {"structure_authoritative": False, "source_generation": source_generation},
            artifacts,
        )
    if not cds_lib_unchanged:
        return ExecutorResult(
            "STALE_SOURCE",
            {"code": "cds_lib_changed", "evidence": evidence},
            {"structure_authoritative": False, "source_generation": source_generation},
            artifacts,
        )
    if not inventory["complete"]:
        return ExecutorResult(
            "FAIL_CADENCE_SI",
            {"code": "required_structure_artifacts_missing", "evidence": evidence},
            {"structure_authoritative": False, "source_generation": source_generation},
            artifacts,
        )
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return ExecutorResult(
            "BLOCKED_TIMEOUT",
            {"code": "post_snapshot_timeout", "evidence": evidence},
            {"structure_authoritative": False, "source_generation": source_generation},
            artifacts,
        )
    post_snapshot = _post_snapshot(replace(context, timeout=remaining))
    post_generation = post_snapshot.outputs.get("source_generation")
    stability_path = root / "source-stability-evidence.json"
    source_stable = (
        post_snapshot.status == "PASS"
        and isinstance(post_generation, str)
        and post_generation == source_generation
    )
    stability = {
        "schema_version": 1,
        "status": "PASS" if source_stable else "NOT_PASS",
        "source_generation_before": source_generation,
        "source_generation_after": post_generation,
        "post_snapshot_status": post_snapshot.status,
        "post_snapshot_summary": dict(post_snapshot.summary),
    }
    write_json_once(stability_path, stability)
    final_artifacts = (*artifacts, *post_snapshot.artifacts, stability_path)
    if post_snapshot.status != "PASS":
        status = (
            post_snapshot.status
            if post_snapshot.status.startswith(("BLOCKED_", "STALE_"))
            else "BLOCKED_INPUT"
        )
        return ExecutorResult(
            status,
            {
                "code": "post_snapshot_not_pass",
                "evidence": evidence,
                "source_stability": stability,
            },
            {"structure_authoritative": False, "source_generation": source_generation},
            final_artifacts,
        )
    if not source_stable:
        return ExecutorResult(
            "STALE_SOURCE",
            {
                "code": "source_generation_changed",
                "evidence": evidence,
                "source_stability": stability,
            },
            {"structure_authoritative": False, "source_generation": source_generation},
            final_artifacts,
        )
    return ExecutorResult(
        "PASS",
        {
            "provider": "cadence_si",
            "source_generation": source_generation,
            "evidence": evidence,
            "source_stability": stability,
        },
        {
            "structure_authoritative": True,
            "source_generation": source_generation,
            "structure_inventory": str(
                inventory_path.relative_to(context.run.payload_root)
            ),
            "si_evidence": str(evidence_path.relative_to(context.run.payload_root)),
            "source_stability_evidence": str(
                stability_path.relative_to(context.run.payload_root)
            ),
            "post_snapshot_evidence": post_snapshot.outputs.get("snapshot_evidence"),
            "netlist": inventory["netlist"],
            "map": inventory["map"],
            "globalmap": inventory["globalmap"],
            "module_maps": inventory["module_maps"],
            "inherited_connections": inventory["inherited_connections"],
            "controls": inventory["controls"],
            "netlister_log": str(log.relative_to(context.run.payload_root)),
        },
        final_artifacts,
    )

def _post_snapshot(context: ExecutorContext) -> ExecutorResult:
    from .virtuoso import run_snapshot
    return run_snapshot(context)

__all__ = ["run_cadence_si"]
