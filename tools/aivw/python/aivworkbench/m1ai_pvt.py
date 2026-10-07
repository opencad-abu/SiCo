"""Scratch-only default-plus-45-PVT Spectre gate for the M1 comparator."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Callable, Iterable, Mapping, Sequence

from . import COMMAND_NAME, PRODUCT_NAME, __version__
from .errors import EnvironmentError
from .m0s_inputs import file_identity, tree_identity
from .m1ai_golden_inputs import (
    load_json_artifact,
    relocate_maestro_project_dirs,
    stage_saradc_library,
    write_scratch_cds_lib,
)
from .m1ai_golden_runtime import (
    PhaseFailure,
    ProcessRunner,
    cadence_environment,
    golden_workers,
    project_path,
    require_log,
    require_under,
    run_phase,
    run_spectre_preflight,
    tool_runtime_version,
    utc_now,
)
from .m1ai_pvt_inputs import (
    collect_pvt_runtime,
    history_rdb_path,
    parse_pvt_rdb,
    runtime_artifact_paths,
)
from .process import ProcessResult, run_process_group
from .profiles import Profile, ToolSpec, product_root
from .toolchain import (
    ToolResult,
    capture_module_environment,
    parse_setup_modules,
    probe_tools,
    safe_environment_summary,
)
from .workspace import LaunchPaths, allocate_run, sha256_file, stable_digest, write_json_once


EnvironmentLoader = Callable[[Sequence[str]], dict[str, str]]
ToolProber = Callable[[Sequence[ToolSpec], Mapping[str, str]], tuple[ToolResult, ...]]


def _artifact_records(root: Path, paths: Iterable[Path]) -> list[dict[str, object]]:
    """Index a bounded evidence set without hashing large PSF waveform databases."""
    records: list[dict[str, object]] = []
    seen: set[Path] = set()
    resolved_root = root.resolve()
    for path in paths:
        if not path.is_file():
            continue
        resolved = path.resolve()
        if resolved in seen or not resolved.is_relative_to(resolved_root):
            continue
        seen.add(resolved)
        records.append(
            {
                "path": resolved.relative_to(resolved_root).as_posix(),
                "size": resolved.stat().st_size,
                "sha256": sha256_file(resolved),
            }
        )
    return sorted(records, key=lambda value: str(value["path"]))


def run_m1_ai_golden_pvt(
    profile: Profile,
    launch: LaunchPaths,
    *,
    timeout: float = 3600.0,
    max_jobs: int = 4,
    environment_loader: EnvironmentLoader | None = None,
    tool_prober: ToolProber | None = None,
    process_runner: ProcessRunner | None = None,
) -> dict[str, object]:
    if not 1 <= max_jobs <= 8:
        raise ValueError("M1 PVT max_jobs must be between 1 and 8")
    started = utc_now()
    adc_root, source_cds_lib = project_path(profile, "root"), project_path(profile, "cds_lib")
    source_oa = adc_root / "DESIGNS" / "GPDK045" / "SARADC" / "oa"
    source_saradc, source_saradc_ii = source_oa / "saradc", source_oa / "saradcII"
    contract = product_root() / "contracts" / "comparator_new.json"
    workers = golden_workers(product_root())
    request = {
        "pilot": "m1-ai",
        "phase": "golden-default-plus-45-pvt",
        "profile_sha256": sha256_file(profile.source_path),
        "contract_sha256": sha256_file(contract),
        "logical_cwd": str(launch.logical_cwd),
        "max_jobs": max_jobs,
    }
    run = allocate_run(launch, "m1-ai-pvt", stable_digest(request))
    work, logs, oa = run.root / "work", run.root / "logs", run.root / "oa"
    work.mkdir()
    logs.mkdir()
    oa.mkdir()
    scratch_saradc = oa / "saradc"
    source_before = tree_identity((source_saradc, source_saradc_ii))
    staging = stage_saradc_library(source_saradc, scratch_saradc)
    physical_work = work.resolve()
    physical_saradc = scratch_saradc.resolve()
    project_dir = physical_work / "simulation"
    relocated = relocate_maestro_project_dirs(
        scratch_saradc / "comparator_new" / "maestro" / "active.state", project_dir
    )
    scratch_cds_lib = work / "cds.lib"
    write_scratch_cds_lib(scratch_cds_lib, source_cds_lib, physical_saradc)
    physical_cds_lib = scratch_cds_lib.resolve()
    input_manifest = run.root / "input-manifest.json"
    write_json_once(
        input_manifest,
        {
            "schema_version": 1,
            "request": request,
            "source_snapshot": source_before,
            "staging": staging,
            "project_dir_relocation": relocated,
            "scratch_cds_lib": file_identity(scratch_cds_lib),
            "workers": {name: file_identity(path) for name, path in workers.items()},
            "source_policy": "source OA read-only; all modifications and results under $RUN",
            "raw_result_policy": "PSF stays under $RUN but is not byte-hashed into manifest",
        },
    )

    setup_modules: tuple[str, ...] = ()
    tools: tuple[ToolResult, ...] = ()
    environment: dict[str, str] = {}
    processes: list[ProcessResult] = []
    evidence: dict[str, object] = {}
    selected_artifacts: list[Path] = [input_manifest]
    status, error = "BLOCKED_ENVIRONMENT", ""
    try:
        setup_modules = parse_setup_modules(profile.setup_script)
        if setup_modules != profile.modules:
            raise EnvironmentError("setup modules differ from the approved profile")
        loader = environment_loader or (lambda values: capture_module_environment(values))
        environment = loader(profile.modules)
        tools = (tool_prober or probe_tools)(profile.tools, environment)
        if not tools or any(item.status != "PASS" for item in tools):
            raise EnvironmentError("one or more EDA tools failed profile qualification")
        paths = {item.name: item.path for item in tools}
        if not paths.get("dbaccess") or not paths.get("virtuoso") or not paths.get("spectre"):
            raise EnvironmentError("qualified toolchain lacks dbAccess, Virtuoso, or Spectre")
        spectre_tool = next(item for item in tools if item.name == "spectre")
        expected_spectre_version = tool_runtime_version(spectre_tool.version_output)
        child = cadence_environment(environment, project=adc_root, work=physical_work)
        runner = process_runner or run_process_group

        preflight_log = logs / "spectre-preflight.log"
        processes.append(
            run_spectre_preflight(
                runner,
                paths["spectre"],
                cwd=physical_work / "spectre-preflight",
                environment=child,
                log_file=preflight_log,
                timeout=min(timeout, 30.0),
                expected_version=expected_spectre_version,
            )
        )
        selected_artifacts.append(preflight_log)

        rebind_json = work / "rebinding.json"
        rebind_log = logs / "dbaccess-rebind.log"
        processes.append(
            run_phase(
                runner,
                (
                    paths["dbaccess"], "-cdslib", str(physical_cds_lib),
                    "-load", str(workers["rebind"]),
                ),
                cwd=physical_work,
                environment={
                    **child,
                    "AIVW_SCRATCH_SARADC": str(physical_saradc),
                    "AIVW_REBIND_OUTPUT": str(rebind_json),
                },
                log_file=rebind_log,
                timeout=min(timeout, 120.0),
                failure_status="FAIL_REBIND",
            )
        )
        require_log(rebind_log, ("AIVW_M1_REBIND_DONE",), "FAIL_REBIND")
        try:
            rebinding = load_json_artifact(rebind_json, provider="dbAccess")
        except (EnvironmentError, OSError, ValueError, json.JSONDecodeError) as exc:
            raise PhaseFailure("FAIL_REBIND_ARTIFACT", str(exc)) from exc
        bindings = rebinding.get("bindings", [])
        if len(bindings) != 2 or not all(item.get("readback_verified") is True for item in bindings):
            raise PhaseFailure("FAIL_REBIND", "scratch DUT rebinding did not verify")
        selected_artifacts.extend((rebind_log, rebind_json))

        sch_log = logs / "virtuoso-schcheck.log"
        sch_console = logs / "schcheck-console.log"
        processes.append(
            run_phase(
                runner,
                (
                    paths["virtuoso"], "-nograph", "-nocdsinit", "-cdslib",
                    str(physical_cds_lib), "-log", str(sch_log), "-replay",
                    str(workers["schcheck"]),
                ),
                cwd=physical_work,
                environment={**child, "AIVW_SCRATCH_SARADC": str(physical_saradc)},
                log_file=sch_console,
                timeout=min(timeout, 120.0),
                failure_status="FAIL_SCHCHECK",
            )
        )
        require_log(
            sch_log,
            (
                "AIVW_M1_SCHCHECK comparator_noise_TB_new errors=0",
                "AIVW_M1_SCHCHECK comparator_offset_TB_new errors=0",
                "AIVW_M1_SCHCHECK_DONE",
            ),
            "FAIL_SCHCHECK",
        )
        selected_artifacts.extend((sch_log, sch_console))

        results = project_dir / "saradc" / "comparator_new" / "maestro" / "results" / "maestro"
        pvt_json = work / "pvt-evidence.json"
        detail_csv, summary_csv = work / "pvt-detail.csv", work / "pvt-summary.csv"
        pvt_log, pvt_console = logs / "virtuoso-pvt.log", logs / "pvt-console.log"
        pvt_environment = {
            **child,
            "AIVW_PROJECT_DIR": str(project_dir),
            "AIVW_SETUP_DB_DIR": str(physical_saradc / "comparator_new" / "maestro"),
            "AIVW_RESULTS_LOCATION": str(results),
            "AIVW_DETAIL_CSV": str(detail_csv),
            "AIVW_SUMMARY_CSV": str(summary_csv),
            "AIVW_PVT_OUTPUT": str(pvt_json),
            "AIVW_MAX_JOBS": str(max_jobs),
        }
        processes.append(
            run_phase(
                runner,
                (
                    paths["virtuoso"], "-nograph", "-nocdsinit", "-cdslib",
                    str(physical_cds_lib), "-log", str(pvt_log), "-replay",
                    str(workers["pvt"]),
                ),
                cwd=physical_work,
                environment=pvt_environment,
                log_file=pvt_console,
                timeout=timeout,
                failure_status="FAIL_PVT_RUN",
            )
        )
        require_log(pvt_log, ("AIVW_M1_PVT_DONE",), "FAIL_PVT_RUN")
        try:
            pvt = load_json_artifact(pvt_json, provider="Virtuoso/ADE Assembler")
        except (EnvironmentError, OSError, ValueError, json.JSONDecodeError) as exc:
            raise PhaseFailure("FAIL_PVT_ARTIFACT", str(exc)) from exc
        require_under(Path(str(pvt["setup_db_dir"])), run.root, label="PVT ADE setup DB")
        require_under(Path(str(pvt["results_location"])), run.root, label="PVT ADE results")
        rdb = history_rdb_path(Path(str(pvt["setup_db_dir"])), str(pvt["history"]))
        try:
            metrics = parse_pvt_rdb(rdb)
            runtime = collect_pvt_runtime(
                results,
                str(pvt["history"]),
                expected_version=expected_spectre_version,
            )
        except (EnvironmentError, OSError, ValueError, sqlite3.DatabaseError) as exc:
            raise PhaseFailure("FAIL_PVT_RESULTS", str(exc)) from exc
        if runtime["license_blocked"]:
            evidence = {
                "rebinding": rebinding,
                "pvt": pvt,
                "metrics": metrics,
                "runtime": runtime,
            }
            raise PhaseFailure(
                "BLOCKED_LICENSE",
                "one or more PVT Spectre points reported SPECTRE-209",
            )
        evidence = {
            "rebinding": rebinding,
            "pvt": pvt,
            "metrics": metrics,
            "runtime": runtime,
        }
        status = (
            "PASS"
            if metrics["all_checks_pass"] and runtime["all_checks_pass"]
            else "FAIL_PVT_GOLDEN"
        )
        if status != "PASS":
            error = "one or more PVT metric, point-matrix, Spectre, or netlist checks failed"
        selected_artifacts.extend(
            (pvt_log, pvt_console, pvt_json, detail_csv, summary_csv, rdb)
        )
        selected_artifacts.extend(runtime_artifact_paths(runtime))
        pvt_evidence = run.root / "golden-pvt-evidence.json"
        write_json_once(pvt_evidence, evidence)
        selected_artifacts.append(pvt_evidence)
    except PhaseFailure as exc:
        if exc.result is not None:
            processes.append(exc.result)
        status, error = exc.status, str(exc)
    except (EnvironmentError, OSError, ValueError, json.JSONDecodeError) as exc:
        error = str(exc)

    source_after = tree_identity((source_saradc, source_saradc_ii))
    sources_unchanged = source_before == source_after
    if not sources_unchanged:
        status, error = "FAIL_SOURCE_MUTATION", "one or more source OA files changed"
    manifest: dict[str, object] = {
        "schema_version": 1,
        "product": {"name": PRODUCT_NAME, "command": COMMAND_NAME, "version": __version__},
        "kind": "m1-ai-golden-pvt",
        "status": status,
        "started_at": started,
        "finished_at": utc_now(),
        "run_id": run.run_id,
        "run_dir": str(run.root),
        "launch": {
            "logical_cwd": str(launch.logical_cwd),
            "physical_cwd": str(launch.physical_cwd),
            "artifact_root": str(launch.logical_artifact_root),
            "artifact_root_physical": str(launch.physical_artifact_root),
        },
        "profile": {"name": profile.name, "path": str(profile.source_path)},
        "setup_modules": list(setup_modules),
        "environment": safe_environment_summary(environment) if environment else {},
        "tools": [item.to_dict() for item in tools],
        "processes": [item.to_dict() for item in processes],
        "verdict_evidence": evidence,
        "source_snapshot_before": source_before,
        "source_snapshot_after": source_after,
        "sources_unchanged": sources_unchanged,
        "next_gate": "AI RNM generation and Spectre-to-RNM correlation",
        "does_not_prove": [
            "comparator mismatch Monte Carlo passes",
            "an AI-generated RNM model is behaviorally correct",
            "the full ADC meets system-level performance requirements",
        ],
        "raw_results": {
            "root": str(project_dir),
            "under_run": project_dir.resolve().is_relative_to(run.root.resolve()),
            "index_policy": "retained under $RUN; large PSF waveform files are not byte-hashed",
        },
        "error": error,
    }
    manifest["artifacts"] = _artifact_records(run.root, selected_artifacts)
    write_json_once(run.manifest, manifest)
    return manifest


__all__ = ["run_m1_ai_golden_pvt"]
