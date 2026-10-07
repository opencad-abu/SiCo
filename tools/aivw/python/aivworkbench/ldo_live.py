"""Read-only live LDO catalog/HED/AMS evidence producer.

This module owns process orchestration and evidence storage only.  It never
writes the source OA tree, changes a config view, or derives an analog verdict
from a successful worker exit code.  The output is a bounded structure report;
official config binding still requires the normalized AMS evidence adapter.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .errors import EnvironmentError, WorkspaceError
from .ldo_qualification import build_source_snapshot
from .ldo_normalize import build_official_ldo_binding, normalize_ldo_structure
from .process import ProcessResult, run_process_group
from .profiles import Profile, ToolSpec, product_root
from .toolchain import (
    ToolResult,
    capture_module_environment,
    parse_setup_modules,
    parse_setup_exports,
    probe_tools,
)
from .workspace import (
    LaunchPaths,
    allocate_split_run,
    resolve_project_db_root,
    sha256_file,
    stable_digest,
    write_json_once,
)
from .ldo_live_contract import LDO_CELLS as _LDO_CELLS
from .ldo_live_contract import project as _project
from .ldo_live_contract import read_worker_json as _read_json
from .ldo_live_evidence import catalog_summary as _catalog_summary
from .ldo_live_evidence import hed_summary as _hed_summary
from .ldo_live_evidence import runams_summary as _runams_summary
from .ldo_live_staging import stage_cds_lib as _stage_cds_lib
from .ldo_live_staging import stage_connect_rule as _stage_connect_rule
from .ldo_live_report import finalize_live_run


EnvironmentLoader = Callable[[Sequence[str]], dict[str, str]]
ToolProber = Callable[[Sequence[ToolSpec], Mapping[str, str]], tuple[ToolResult, ...]]
ProcessRunner = Callable[..., ProcessResult]


def _run_phase(
    runner: ProcessRunner,
    command: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    log_file: Path,
    timeout: float,
) -> ProcessResult:
    result = runner(
        tuple(str(item) for item in command),
        cwd=cwd,
        environment=environment,
        log_file=log_file,
        timeout=timeout,
    )
    return result


def run_ldo_live(
    profile: Profile,
    launch: LaunchPaths,
    *,
    timeout: float = 300.0,
    environment_loader: EnvironmentLoader | None = None,
    tool_prober: ToolProber | None = None,
    process_runner: ProcessRunner | None = None,
    runams: bool = True,
) -> dict[str, Any]:
    """Collect live LDO structure evidence in a split-storage run."""

    if timeout <= 0:
        raise ValueError("LDO live timeout must be positive")
    project = _project(profile)
    source_root = Path(str(project["root"])).expanduser().resolve(strict=True)
    source_cds = Path(str(project["cds_lib"])).expanduser().resolve(strict=True)
    source_original_cds = (
        Path(str(project.get("source_cds_lib", source_cds)))
        .expanduser()
        .resolve(strict=True)
    )
    mapping_environment = {
        str(key): str(value)
        for key, value in (project.get("mapping_environment", {}) or {}).items()
    }
    source_before = build_source_snapshot(
        source_root,
        library="amsLDO",
        cds_lib=source_cds,
        mapping_root=profile.workspace_root,
        mapping_allowed_roots=project.get("mapping_allowed_roots"),
        mapping_environment=mapping_environment,
        source_cds_lib=source_original_cds,
        model_root=project.get("model_root"),
        required_library_mappings=tuple(
            project.get(
                "required_library_mappings", ("amsLDO", "gpdk045", "analogLib", "basic")
            )
        ),
        required_model_sections=tuple(
            project.get("required_model_sections", ("tt", "ff", "ss"))
        ),
    )
    request = {
        "schema_version": 1,
        "kind": "ldo-live-structure",
        "profile": profile.name,
        "profile_sha256": sha256_file(profile.source_path),
        "source_generation": source_before.get("source_generation"),
        "target": {
            "library": "amsLDO",
            "cells": list(_LDO_CELLS),
            "config_view": "config",
        },
        "workers": {
            "catalog": str(product_root() / "skill" / "ldo_catalog_worker.il"),
            "hed": str(product_root() / "skill" / "ldo_hed_worker.il"),
        },
        "runams_requested": bool(runams),
    }
    request_digest = stable_digest(request)
    if profile.storage is None:
        raise WorkspaceError(f"profile {profile.name!r} has no split-storage contract")
    environment_for_storage = parse_setup_exports(
        profile.setup_script, (profile.storage.payload_env,)
    )
    payload_root = resolve_project_db_root(
        environment_for_storage[profile.storage.payload_env],
        launch,
        forbidden_roots=(product_root(),),
    )
    run = allocate_split_run(
        launch,
        payload_root,
        library="amsLDO",
        cell="LDO_MASTER",
        view="config",
        kind="ldo-live",
        request_digest=request_digest,
    )
    write_json_once(run.control_root / "request.json", request)
    payload = run.payload_root
    worker_root = payload / "workers"
    logs = payload / "logs"
    runams_root = payload / "runams"
    for path in (worker_root, logs, runams_root):
        path.mkdir(parents=True, exist_ok=True)
    staged_cds = payload / "cds.lib"
    staged_rule = payload / "connect-rules" / "CR_full_fast.vams"
    setup_modules: tuple[str, ...] = ()
    tools: tuple[ToolResult, ...] = ()
    environment: dict[str, str] = {}
    processes: list[ProcessResult] = []
    authenticated_snapshot: Mapping[str, Any] | None = None
    findings: list[str] = []
    catalog_summary: dict[str, Any] = {}
    hed_summaries: list[dict[str, Any]] = []
    runams_summaries: list[dict[str, Any]] = []
    official_bindings: list[dict[str, Any]] = []
    status = "BLOCKED_ENVIRONMENT"
    error = ""
    try:
        setup_modules = parse_setup_modules(profile.setup_script)
        if setup_modules != profile.modules:
            raise EnvironmentError("setup modules differ from approved profile")
        loader = environment_loader or (
            lambda values: capture_module_environment(values)
        )
        environment = loader(profile.modules)
        tools = (tool_prober or probe_tools)(profile.tools, environment)
        paths = {item.name: item.path for item in tools if item.status == "PASS"}
        if any(item.status != "PASS" for item in tools) or not all(
            paths.get(name) for name in ("dbaccess", "virtuoso")
        ):
            raise EnvironmentError("qualified dbAccess and Virtuoso tools are required")
        approved_mapping = source_before.get("cds_lib", {}).get("mapping", {})
        _stage_cds_lib(
            staged_cds,
            source_root=source_root,
            tool_environment=environment,
            approved_mapping=approved_mapping,
        )
        _stage_connect_rule(
            staged_rule,
            tool_environment=environment,
            policy=_project(profile).get("connect_rule", {}),
        )
        child = dict(environment)
        child.update(
            {
                "CDS_LIB": str(staged_cds),
                "CDS_CDSLIB": str(staged_cds),
                "PROJECT": str(source_root),
                "PWD": str(payload),
            }
        )
        runner = process_runner or run_process_group
        catalog_path = worker_root / "catalog.json"
        catalog_env = {
            **child,
            "AIVW_LDO_CATALOG_OUTPUT": str(catalog_path),
            "AIVW_LDO_LIBRARY": "amsLDO",
            "AIVW_LDO_CELLS": ",".join(_LDO_CELLS),
        }
        processes.append(
            _run_phase(
                runner,
                (
                    paths["dbaccess"],
                    "-load",
                    str(product_root() / "skill" / "ldo_catalog_worker.il"),
                ),
                cwd=payload,
                environment=catalog_env,
                log_file=logs / "dbaccess.log",
                timeout=timeout,
            )
        )
        catalog = _read_json(catalog_path, "catalog")
        catalog_summary, catalog_findings = _catalog_summary(catalog)
        findings.extend(catalog_findings)
        for cell in _LDO_CELLS:
            hed_path = worker_root / f"hed-{cell}.json"
            hed_env = {
                **child,
                "AIVW_LDO_HED_OUTPUT": str(hed_path),
                "AIVW_LDO_SOURCE_GENERATION": str(
                    source_before.get("source_generation") or ""
                ),
                "AIVW_LDO_CONFIG_LIBRARY": "amsLDO",
                "AIVW_LDO_CONFIG_CELL": cell,
                "AIVW_LDO_CONFIG_VIEW": "config",
            }
            processes.append(
                _run_phase(
                    runner,
                    (
                        paths["virtuoso"],
                        "-nograph",
                        "-nocdsinit",
                        "-cdslib",
                        str(staged_cds),
                        "-log",
                        str(logs / f"virtuoso-{cell}.log"),
                        "-replay",
                        str(product_root() / "skill" / "ldo_hed_worker.il"),
                    ),
                    cwd=payload,
                    environment=hed_env,
                    log_file=logs / f"hed-{cell}.console.log",
                    timeout=timeout,
                )
            )
            hed = _read_json(hed_path, f"HED {cell}")
            summary, hed_findings = _hed_summary(hed, cell)
            hed_summaries.append(summary)
            findings.extend(hed_findings)
        # Promote the two independent read-only HED checks into the source
        # snapshot authentication contract only when both agree on identity
        # and the expected source generation.
        hed_auth = (
            len(hed_summaries) == len(_LDO_CELLS)
            and all(item.get("authenticated") is True for item in hed_summaries)
            and all(
                item.get("source_generation") == source_before.get("source_generation")
                for item in hed_summaries
            )
        )
        if hed_auth:
            authenticated_snapshot = {
                "schema_version": 1,
                "status": "PASS",
                "authority": "authenticated-cdns-ipc-read-only",
                "formal_saved_state": True,
                "double_read_equal": True,
                "source_generation": source_before.get("source_generation"),
                "target": {"library": "amsLDO", "cells": list(_LDO_CELLS)},
                "snapshot_digest": stable_digest(
                    {
                        "source_generation": source_before.get("source_generation"),
                        "hed": hed_summaries,
                    }
                ),
            }
            source_before = build_source_snapshot(
                source_root,
                library="amsLDO",
                cds_lib=source_cds,
                source_cds_lib=source_original_cds,
                mapping_root=profile.workspace_root,
                mapping_allowed_roots=project.get("mapping_allowed_roots"),
                mapping_environment=mapping_environment,
                model_root=project.get("model_root"),
                required_library_mappings=tuple(
                    project.get(
                        "required_library_mappings",
                        ("amsLDO", "gpdk045", "analogLib", "basic"),
                    )
                ),
                required_model_sections=tuple(
                    project.get("required_model_sections", ("tt", "ff", "ss"))
                ),
                authenticated_snapshot=authenticated_snapshot,
            )
        if runams and paths.get("runams"):
            for cell in _LDO_CELLS:
                rundir = runams_root / cell
                log = logs / f"runams-{cell}.log"
                result = _run_phase(
                    runner,
                    (
                        paths["runams"],
                        "-nocdsinit",
                        "-lib",
                        "amsLDO",
                        "-cell",
                        cell,
                        "-view",
                        "config",
                        "-netlist",
                        "all",
                        "-savescripts",
                        "-clean",
                        "-reportinvalidbinding",
                        "-netlisteropts",
                        "amsPortConnectionByNameOrOrder=order",
                        "-cdslib",
                        str(staged_cds),
                        "-connectrules",
                        f"userDef:CR_full_fast:{staged_rule}",
                        "-rundir",
                        str(rundir),
                        "-log",
                        str(log),
                    ),
                    cwd=payload,
                    environment=child,
                    log_file=logs / f"runams-{cell}.console.log",
                    timeout=timeout,
                )
                processes.append(result)
                summary = _runams_summary(rundir, log, result, payload_root=payload)
                summary["cell"] = cell
                runams_summaries.append(summary)
                if result.returncode != 0 or result.timed_out:
                    findings.append(f"{cell} runams did not complete successfully")
                if summary["netlist_status"] != "success":
                    findings.append(f"{cell} runams netlist status is not success")
                if summary["success_marker"] is not True:
                    findings.append(f"{cell} runams success marker is missing")
                if summary["fatal_log_marker"] is True:
                    findings.append(f"{cell} runams log contains a fatal marker")
                if summary["unresolved_count"] != 0:
                    findings.append(f"{cell} runams has unresolved bindings")
                required = summary["required_artifacts"]
                if isinstance(required, Mapping):
                    for artifact_name, present in required.items():
                        if present is not True:
                            findings.append(
                                f"{cell} runams artifact is missing: {artifact_name}"
                            )
                if summary["connect_rule_evidence"] is not True:
                    findings.append(
                        f"{cell} connect-rule artifact was not produced in payload"
                    )
                # Parse each completed run into a bounded, adapter-ready
                # normalized evidence artifact.  This remains offline after
                # runams exits and never promotes the analog behavior verdict.
                try:
                    normalized, normalized_evidence = normalize_ldo_structure(
                        cell=cell,
                        hed_path=worker_root / f"hed-{cell}.json",
                        catalog_path=catalog_path,
                        runams_root=rundir,
                        payload_root=payload,
                        runams_log=log,
                    )
                    write_json_once(payload / f"normalized-{cell}.json", normalized)
                    summary["normalized_evidence"] = normalized_evidence
                except (WorkspaceError, OSError, ValueError) as exc:
                    findings.append(
                        f"{cell} normalized AMS evidence unavailable: {exc}"
                    )
        if source_before.get("status") != "PASS":
            findings.append(
                "source preflight is not PASS; live evidence cannot qualify the original mapping"
            )
            for item in source_before.get("errors", ()):
                if isinstance(item, str):
                    findings.append("source preflight: " + item)
        else:
            # Generate the official structure binding before publishing the
            # payload manifest.  The adapter is offline and fail-closed; its
            # output is therefore included in the same immutable artifact
            # index as the normalized HED/runams evidence.
            for cell in _LDO_CELLS:
                try:
                    official_bindings.append(
                        build_official_ldo_binding(
                            cell=cell,
                            payload_root=payload,
                            source_generation=str(source_before["source_generation"]),
                            source_preflight=source_before,
                            target={
                                "library": "amsLDO",
                                "cell": cell,
                                "config_view": "config",
                            },
                        )
                    )
                except (WorkspaceError, OSError, ValueError) as exc:
                    findings.append(
                        f"{cell} official config binding unavailable: {exc}"
                    )
        if findings:
            status = "BLOCKED_INPUT"
        else:
            status = "PASS"
        if not (payload / "source-preflight-before.json").exists():
            write_json_once(payload / "source-preflight-before.json", source_before)
        write_json_once(
            payload / "catalog-summary.json", {"schema_version": 1, **catalog_summary}
        )
        write_json_once(
            payload / "hed-summary.json", {"schema_version": 1, "cells": hed_summaries}
        )
        write_json_once(
            payload / "runams-summary.json",
            {"schema_version": 1, "cells": runams_summaries, "requested": bool(runams)},
        )
        write_json_once(
            payload / "official-binding-summary.json",
            {"schema_version": 1, "bindings": official_bindings},
        )
    except (EnvironmentError, OSError, ValueError, WorkspaceError) as exc:
        error = str(exc)
        status = "BLOCKED_ENVIRONMENT" if not processes else "BLOCKED_INPUT"

    source_after = build_source_snapshot(
        source_root,
        library="amsLDO",
        cds_lib=source_cds,
        mapping_root=profile.workspace_root,
        mapping_allowed_roots=project.get("mapping_allowed_roots"),
        mapping_environment=mapping_environment,
        source_cds_lib=source_original_cds,
        authenticated_snapshot=authenticated_snapshot,
        model_root=project.get("model_root"),
        required_library_mappings=tuple(
            project.get(
                "required_library_mappings", ("amsLDO", "gpdk045", "analogLib", "basic")
            )
        ),
        required_model_sections=tuple(
            project.get("required_model_sections", ("tt", "ff", "ss"))
        ),
    )
    if source_before.get("source_generation") != source_after.get("source_generation"):
        findings.append("source generation changed during live read-only run")
        status = "STALE_SOURCE"
    if not (payload / "source-preflight-before.json").exists():
        write_json_once(payload / "source-preflight-before.json", source_before)
    return finalize_live_run(
        run=run,
        payload=payload,
        request=request,
        request_digest=request_digest,
        profile_name=profile.name,
        source_before=source_before,
        source_after=source_after,
        setup_modules=setup_modules,
        environment=environment,
        tools=tools,
        processes=processes,
        staged_cds=staged_cds,
        staged_rule=staged_rule,
        catalog_summary=catalog_summary,
        hed_summaries=hed_summaries,
        runams_summaries=runams_summaries,
        official_bindings=official_bindings,
        findings=findings,
        error=error,
        status=status,
    )


__all__ = ["run_ldo_live"]
