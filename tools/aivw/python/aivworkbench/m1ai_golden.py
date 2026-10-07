"""Scratch-only nominal Spectre qualification for the M1 comparator target."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Mapping, Sequence

from . import COMMAND_NAME, PRODUCT_NAME, __version__
from .errors import EnvironmentError
from .m0s_inputs import file_identity, tree_identity
from .m1ai_golden_inputs import (
    collect_nominal_result_evidence,
    load_json_artifact,
    relocate_maestro_project_dirs,
    stage_saradc_library,
    write_scratch_cds_lib,
)
from .m1ai_golden_runtime import (
    PhaseFailure,
    ProcessRunner,
    artifact_index,
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
def run_m1_ai_golden_nominal(
    profile: Profile,
    launch: LaunchPaths,
    *,
    timeout: float = 300.0,
    environment_loader: EnvironmentLoader | None = None,
    tool_prober: ToolProber | None = None,
    process_runner: ProcessRunner | None = None,
) -> dict[str, object]:
    started = utc_now()
    adc_root, source_cds_lib = project_path(profile, "root"), project_path(profile, "cds_lib")
    source_oa = adc_root / "DESIGNS" / "GPDK045" / "SARADC" / "oa"
    source_saradc, source_saradc_ii = source_oa / "saradc", source_oa / "saradcII"
    contract = product_root() / "contracts" / "comparator_new.json"
    workers = golden_workers(product_root())
    request = {
        "pilot": "m1-ai",
        "phase": "golden-nominal-offset",
        "profile_sha256": sha256_file(profile.source_path),
        "contract_sha256": sha256_file(contract),
        "logical_cwd": str(launch.logical_cwd),
    }
    run = allocate_run(launch, "m1-ai-golden", stable_digest(request))
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
    write_json_once(
        run.root / "input-manifest.json",
        {
            "schema_version": 1,
            "request": request,
            "source_snapshot": source_before,
            "staging": staging,
            "project_dir_relocation": relocated,
            "scratch_cds_lib": file_identity(scratch_cds_lib),
            "workers": {name: file_identity(path) for name, path in workers.items()},
            "source_policy": "source OA read-only; all modifications and results under $RUN",
        },
    )

    setup_modules: tuple[str, ...] = ()
    tools: tuple[ToolResult, ...] = ()
    environment: dict[str, str] = {}
    processes: list[ProcessResult] = []
    evidence: dict[str, object] = {}
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
        processes.append(
            run_spectre_preflight(
                runner,
                paths["spectre"],
                cwd=physical_work / "spectre-preflight",
                environment=child,
                log_file=logs / "spectre-preflight.log",
                timeout=min(timeout, 30.0),
                expected_version=expected_spectre_version,
            )
        )
        rebind_json = work / "rebinding.json"
        rebind_env = {
            **child,
            "AIVW_SCRATCH_SARADC": str(physical_saradc),
            "AIVW_REBIND_OUTPUT": str(rebind_json),
        }
        processes.append(
            run_phase(
                runner,
                (paths["dbaccess"], "-cdslib", str(physical_cds_lib), "-load", str(workers["rebind"])),
                cwd=physical_work,
                environment=rebind_env,
                log_file=logs / "dbaccess-rebind.log",
                timeout=min(timeout, 120.0),
                failure_status="FAIL_REBIND",
            )
        )
        require_log(logs / "dbaccess-rebind.log", ("AIVW_M1_REBIND_DONE",), "FAIL_REBIND")
        try:
            rebinding = load_json_artifact(rebind_json, provider="dbAccess")
        except (EnvironmentError, OSError, ValueError, json.JSONDecodeError) as exc:
            raise PhaseFailure("FAIL_REBIND_ARTIFACT", str(exc)) from exc
        bindings = rebinding.get("bindings", [])
        if len(bindings) != 2 or not all(item.get("readback_verified") is True for item in bindings):
            raise PhaseFailure("FAIL_REBIND", "scratch DUT rebinding did not verify")

        sch_log = logs / "virtuoso-schcheck.log"
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
                log_file=logs / "schcheck-console.log",
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

        results = project_dir / "saradc" / "comparator_new" / "maestro" / "results" / "maestro"
        nominal_json = work / "nominal-evidence.json"
        detail_csv, summary_csv = work / "nominal-detail.csv", work / "nominal-summary.csv"
        nominal_log = logs / "virtuoso-nominal.log"
        nominal_env = {
            **child,
            "AIVW_PROJECT_DIR": str(project_dir),
            "AIVW_SETUP_DB_DIR": str(physical_saradc / "comparator_new" / "maestro"),
            "AIVW_RESULTS_LOCATION": str(results),
            "AIVW_DETAIL_CSV": str(detail_csv),
            "AIVW_SUMMARY_CSV": str(summary_csv),
            "AIVW_NOMINAL_OUTPUT": str(nominal_json),
        }
        processes.append(
            run_phase(
                runner,
                (
                    paths["virtuoso"], "-nograph", "-nocdsinit", "-cdslib",
                    str(physical_cds_lib), "-log", str(nominal_log), "-replay",
                    str(workers["nominal"]),
                ),
                cwd=physical_work,
                environment=nominal_env,
                log_file=logs / "nominal-console.log",
                timeout=timeout,
                failure_status="FAIL_NOMINAL_RUN",
            )
        )
        require_log(nominal_log, ("AIVW_M1_NOMINAL_DONE",), "FAIL_NOMINAL_RUN")
        try:
            nominal = load_json_artifact(nominal_json, provider="Virtuoso/ADE Assembler")
        except (EnvironmentError, OSError, ValueError, json.JSONDecodeError) as exc:
            raise PhaseFailure("FAIL_NOMINAL_ARTIFACT", str(exc)) from exc
        require_under(Path(str(nominal["setup_db_dir"])), run.root, label="ADE setup DB")
        require_under(
            Path(str(nominal["results_location"])),
            run.root,
            label="ADE results location",
        )
        try:
            result_evidence = collect_nominal_result_evidence(
                results,
                str(nominal["history"]),
                detail_csv,
                expected_version=expected_spectre_version,
            )
        except (EnvironmentError, OSError, ValueError) as exc:
            raise PhaseFailure("FAIL_NOMINAL_RESULTS", str(exc)) from exc
        runtime = result_evidence["runtime"]
        if runtime["license_blocked"]:
            evidence = {"rebinding": rebinding, "nominal": nominal, "runtime": runtime}
            write_json_once(run.root / "golden-nominal-evidence.json", evidence)
            raise PhaseFailure(
                "BLOCKED_LICENSE",
                "Spectre FATAL (SPECTRE-209): required license was unavailable",
            )
        metric = result_evidence["metric"]
        evidence = {
            "rebinding": rebinding,
            "nominal": nominal,
            "metric": metric,
            "runtime": runtime,
        }
        status = "PASS" if metric["passes"] and runtime["all_checks_pass"] else "FAIL_GOLDEN"
        if status != "PASS":
            error = "nominal Offset/spec verdict or Spectre/netlist runtime checks failed"
        write_json_once(run.root / "golden-nominal-evidence.json", evidence)
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
        "kind": "m1-ai-golden-nominal",
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
        "next_gate": "45-corner offset and PSS/Pnoise qualification",
        "does_not_prove": [
            "45-corner comparator_new offset passes",
            "comparator_new PSS/Pnoise passes",
            "an AI-generated RNM model is behaviorally correct",
        ],
        "error": error,
    }
    manifest["artifacts"] = artifact_index(run.root, {run.manifest})
    write_json_once(run.manifest, manifest)
    return manifest


__all__ = ["run_m1_ai_golden_nominal"]
