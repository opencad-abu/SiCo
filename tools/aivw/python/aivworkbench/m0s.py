"""M0-S PLL config structural qualification runner."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Callable, Mapping, Sequence

from . import COMMAND_NAME, PRODUCT_NAME, __version__
from .errors import EnvironmentError, ProfileError
from .m0s_inputs import file_identity, parse_expand_config, stage_state, tree_identity
from .m0s_normalize import normalize_structure
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
ProcessRunner = Callable[..., ProcessResult]


class _PhaseFailure(Exception):
    def __init__(self, status: str, detail: str, result: ProcessResult) -> None:
        super().__init__(detail)
        self.status = status
        self.result = result


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _project_path(profile: Profile, project: str, key: str) -> Path:
    value = profile.projects.get(project, {}).get(key)
    if not value:
        raise ProfileError(f"profile project {project!r} has no {key!r}")
    path = Path(str(value)).expanduser()
    if not path.is_absolute() or "smic28" in str(path).lower():
        raise ProfileError(f"unsafe profile project path: {path}")
    return path


def _input(profile: Profile, name: str) -> Path:
    item = next((value for value in profile.inputs if value.name == name), None)
    if item is None:
        raise ProfileError(f"profile has no {name} qualification input")
    return item.path


def _run_phase(
    runner: ProcessRunner,
    command: tuple[str, ...],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    log_file: Path,
    timeout: float,
    failure_status: str,
) -> ProcessResult:
    result = runner(command, cwd=cwd, environment=environment, log_file=log_file, timeout=timeout)
    text = log_file.read_text(encoding="utf-8", errors="replace") if log_file.is_file() else ""
    lowered = text.lower()
    if result.timed_out:
        raise _PhaseFailure("TIMEOUT", f"{command[0]} timed out", result)
    if "license checkout failed" in lowered or "no valid license" in lowered:
        raise _PhaseFailure(
            "BLOCKED_LICENSE", f"{command[0]} could not obtain a license", result
        )
    if result.returncode != 0:
        raise _PhaseFailure(
            failure_status, f"{command[0]} exited with {result.returncode}", result
        )
    return result


def _artifact_index(root: Path, excluded: set[Path]) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path not in excluded:
            records.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "size": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
    return records


def run_m0_s(
    profile: Profile,
    launch: LaunchPaths,
    *,
    timeout: float = 300.0,
    environment_loader: EnvironmentLoader | None = None,
    tool_prober: ToolProber | None = None,
    process_runner: ProcessRunner | None = None,
) -> dict[str, object]:
    started = _now()
    pll_root = _project_path(profile, "pll", "root")
    cds_lib = _project_path(profile, "pll", "cds_lib")
    config_path = _input(profile, "m0_s_config")
    state_source = _input(profile, "m0_s_ams_state").parent
    oa_root = pll_root / "DESIGNS" / "GPDK045" / "FRACNPLL" / "oa"
    source_roots = (oa_root / "zambezi45", oa_root / "zambezi45_sim", oa_root / "zambezi45_connectLib")
    source_files = (cds_lib, cds_lib.parent / "common.lib")
    connect_dir = oa_root / "zambezi45_connectLib"
    connect_rules = connect_dir / "ConnRules.vams"
    config = parse_expand_config(config_path)
    request = {
        "pilot": "m0-s",
        "profile": profile.name,
        "profile_sha256": sha256_file(profile.source_path),
        "config_sha256": sha256_file(config_path),
        "state_info_sha256": sha256_file(state_source / "ADE_state.info"),
        "logical_cwd": str(launch.logical_cwd),
    }
    run = allocate_run(launch, "m0-s", stable_digest(request))
    work, logs = run.root / "work", run.root / "logs"
    work.mkdir()
    logs.mkdir()
    source_before = {
        "trees": tree_identity(source_roots),
        "files": {str(path): file_identity(path) for path in source_files},
    }
    staged_state = stage_state(
        state_source,
        work / "state-root",
        library="zambezi45_sim",
        cell="pll_sim",
        simulator="ams",
        state_name="ams_function",
    )
    hed_script = product_root() / "skill" / "m0s_hed_worker.il"
    catalog_script = product_root() / "skill" / "m0s_catalog_worker.il"
    write_json_once(
        run.root / "input-manifest.json",
        {
            "schema_version": 1,
            "request": request,
            "config": config,
            "source_snapshot": source_before,
            "staged_state": staged_state,
            "adapters": {
                "connect_include": str(connect_dir),
                "connect_rules": file_identity(connect_rules),
                "port_connection": "order",
            },
            "workers": {"hed": file_identity(hed_script), "catalog": file_identity(catalog_script)},
            "source_policy": "read_only",
        },
    )

    setup_modules: tuple[str, ...] = ()
    tools: tuple[ToolResult, ...] = ()
    environment: dict[str, str] = {}
    processes: list[ProcessResult] = []
    evidence: dict[str, object] = {}
    canonical: dict[str, object] = {}
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
        for required in ("dbaccess", "virtuoso", "runams"):
            if not paths.get(required):
                raise EnvironmentError(f"qualified toolchain has no {required}")
        child = dict(environment)
        child.update(
            {
                "PROJECT": str(pll_root),
                "CDS_SITE": str(pll_root / "setup" / "site"),
                "CDS_DB_TYPE": "oa",
                "CDS_AUTO_64BIT": "ALL",
                "PWD": str(work),
            }
        )
        runner = process_runner or run_process_group
        catalog_json = work / "catalog.json"
        catalog_env = {**child, "AIVW_CATALOG_OUTPUT": str(catalog_json)}
        processes.append(
            _run_phase(
                runner,
                (paths["dbaccess"], "-cdslib", str(cds_lib), "-load", str(catalog_script)),
                cwd=work,
                environment=catalog_env,
                log_file=logs / "dbaccess-console.log",
                timeout=timeout,
                failure_status="FAIL_CATALOG",
            )
        )
        hed_json = work / "hed.json"
        hed_env = {
            **child,
            "AIVW_HED_OUTPUT": str(hed_json),
            "AIVW_CONFIG_LIB": "zambezi45_sim",
            "AIVW_CONFIG_CELL": "pll_sim",
            "AIVW_CONFIG_VIEW": "config_function",
        }
        processes.append(
            _run_phase(
                runner,
                (
                    paths["virtuoso"], "-nograph", "-nocdsinit", "-cdslib", str(cds_lib),
                    "-log", str(logs / "virtuoso.log"), "-replay", str(hed_script),
                ),
                cwd=work,
                environment=hed_env,
                log_file=logs / "hed-console.log",
                timeout=timeout,
                failure_status="FAIL_HED",
            )
        )
        runams_dir = work / "runams"
        runams_log = logs / "runams.log"
        processes.append(
            _run_phase(
                runner,
                (
                    paths["runams"], "-nocdsinit", "-lib", "zambezi45_sim", "-cell", "pll_sim",
                    "-view", "config_function", "-state", str(staged_state["state_argument"]),
                    "-netlist", "all", "-savescripts", "-clean", "-reportinvalidbinding",
                    "-netlisteropts", "amsPortConnectionByNameOrOrder=order",
                    "-incdir", str(connect_dir),
                    "-connectrules", f"userDef:ConnRules:{connect_rules}",
                    "-cdslib", str(cds_lib), "-rundir", str(runams_dir), "-log", str(runams_log),
                ),
                cwd=work,
                environment=child,
                log_file=logs / "runams-console.log",
                timeout=timeout,
                failure_status="FAIL_AMS_NETLIST",
            )
        )
        canonical, evidence = normalize_structure(
            hed_path=hed_json,
            catalog_path=catalog_json,
            config=config,
            runams_log=runams_log,
            netlist_root=runams_dir / "netlist",
        )
        required_checks = (
            evidence.get("top_matches"), evidence.get("hed_traversal_ok"),
            evidence.get("catalog_authoritative"), evidence.get("catalog_top_opened"),
            evidence.get("success_marker"), evidence.get("netlist_status") == "success",
            evidence.get("unresolved_count") == 0, not evidence.get("missing_artifacts"),
            not evidence.get("fatal_log_marker"), int(evidence.get("binding_count", 0)) > 0,
            evidence.get("explicit_cell_bindings_preserved"),
            evidence.get("explicit_instance_bindings_accounted"),
        )
        status = "PASS" if all(required_checks) else "FAIL_STRUCTURAL"
        write_json_once(run.root / "canonical-structure.json", canonical)
    except _PhaseFailure as exc:
        processes.append(exc.result)
        status, error = exc.status, str(exc)
    except (EnvironmentError, OSError, ValueError, json.JSONDecodeError) as exc:
        error = str(exc)

    source_after = {
        "trees": tree_identity(source_roots),
        "files": {str(path): file_identity(path) for path in source_files},
    }
    sources_unchanged = source_before == source_after
    if not sources_unchanged:
        status = "FAIL_SOURCE_MUTATION"
    manifest: dict[str, object] = {
        "schema_version": 1,
        "product": {"name": PRODUCT_NAME, "command": COMMAND_NAME, "version": __version__},
        "kind": "m0-s",
        "status": status,
        "started_at": started,
        "finished_at": _now(),
        "run_id": run.run_id,
        "run_dir": str(run.root),
        "launch": {
            "logical_cwd": str(launch.logical_cwd),
            "physical_cwd": str(launch.physical_cwd),
            "artifact_root": str(launch.logical_artifact_root),
        },
        "profile": {"name": profile.name, "path": str(profile.source_path)},
        "setup_modules": list(setup_modules),
        "environment": safe_environment_summary(environment) if environment else {},
        "tools": [item.to_dict() for item in tools],
        "processes": [item.to_dict() for item in processes],
        "verdict_evidence": evidence,
        "canonical_structure": canonical,
        "source_snapshot_before": source_before,
        "source_snapshot_after": source_after,
        "sources_unchanged": sources_unchanged,
        "staged_state": staged_state,
        "error": error,
    }
    manifest["artifacts"] = _artifact_index(run.root, {run.manifest})
    write_json_once(run.manifest, manifest)
    return manifest
