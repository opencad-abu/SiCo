"""M1-AI comparator input/interface/spec qualification runner."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Callable, Mapping, Sequence
import xml.etree.ElementTree as ET

from . import COMMAND_NAME, PRODUCT_NAME, __version__
from .errors import EnvironmentError, ProfileError
from .m0s_inputs import file_identity
from .m1ai_inputs import (
    load_contract,
    parse_history_rdb,
    parse_maestro,
    qualify_inputs,
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
ProcessRunner = Callable[..., ProcessResult]


class _DbAccessArtifactError(Exception):
    """The dbAccess process returned without a usable evidence artifact."""


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


def _snapshot(paths: Sequence[Path]) -> dict[str, object]:
    return {str(path): file_identity(path) for path in paths}


def _artifact_index(root: Path, excluded: set[Path]) -> list[dict[str, object]]:
    return [
        {
            "path": path.relative_to(root).as_posix(),
            "size": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in sorted(root.rglob("*"))
        if path.is_file() and path not in excluded
    ]


def _load_oa_evidence(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise _DbAccessArtifactError(f"dbAccess evidence artifact is missing: {path}")
    if path.stat().st_size == 0:
        raise _DbAccessArtifactError(f"dbAccess evidence artifact is empty: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _DbAccessArtifactError(
            f"dbAccess evidence artifact is not valid JSON: {path}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise _DbAccessArtifactError("dbAccess evidence artifact root is not an object")
    if payload.get("schema_version") != 1:
        raise _DbAccessArtifactError("dbAccess evidence artifact has unsupported schema")
    if payload.get("provider") != "dbAccess" or payload.get("read_only") is not True:
        raise _DbAccessArtifactError(
            "dbAccess evidence artifact does not assert provider=dbAccess and read_only=true"
        )
    cellviews = payload.get("cellviews")
    if not isinstance(cellviews, list) or not cellviews:
        raise _DbAccessArtifactError("dbAccess evidence artifact has no cellviews")
    return payload


def run_m1_ai_qualification(
    profile: Profile,
    launch: LaunchPaths,
    *,
    timeout: float = 120.0,
    environment_loader: EnvironmentLoader | None = None,
    tool_prober: ToolProber | None = None,
    process_runner: ProcessRunner | None = None,
) -> dict[str, object]:
    started = _now()
    adc_root = _project_path(profile, "adc", "root")
    cds_lib = _project_path(profile, "adc", "cds_lib")
    oa_root = adc_root / "DESIGNS" / "GPDK045" / "SARADC" / "oa"
    maestro_root = _input(profile, "m1_ai_maestro")
    manual = _input(profile, "m1_ai_manual")
    contract_path = product_root() / "contracts" / "comparator_new.json"
    worker = product_root() / "skill" / "m1ai_qualify_worker.il"
    history = maestro_root / "results" / "maestro" / "Corner%20Analysis.rdb"
    source_files = (
        maestro_root / "maestro.sdb",
        maestro_root / "active.state",
        maestro_root / "data.dm",
        history,
        manual,
        oa_root / "saradc" / "comparator" / "schematic" / "sch.oa",
        oa_root / "saradc" / "comparator" / "symbol" / "symbol.oa",
        oa_root / "saradcII" / "comparator" / "schematic" / "sch.oa",
        oa_root / "saradcII" / "comparator" / "symbol" / "symbol.oa",
        oa_root / "saradc" / "comparator_new" / "schematic" / "sch.oa",
        oa_root / "saradc" / "comparator_new" / "symbol" / "symbol.oa",
        oa_root / "saradc" / "comparator_noise_TB_new" / "schematic" / "sch.oa",
        oa_root / "saradc" / "comparator_offset_TB_new" / "schematic" / "sch.oa",
    )
    missing = [str(path) for path in source_files if not path.is_file()]
    if missing:
        raise ProfileError(f"M1 qualification inputs are missing: {missing}")
    contract = load_contract(contract_path)
    request = {
        "pilot": "m1-ai",
        "phase": "input-qualification",
        "profile": profile.name,
        "profile_sha256": sha256_file(profile.source_path),
        "contract_sha256": sha256_file(contract_path),
        "maestro_sha256": sha256_file(maestro_root / "maestro.sdb"),
        "manual_sha256": sha256_file(manual),
        "logical_cwd": str(launch.logical_cwd),
    }
    run = allocate_run(launch, "m1-ai-inputs", stable_digest(request))
    work, logs = run.root / "work", run.root / "logs"
    work.mkdir()
    logs.mkdir()
    source_before = _snapshot(source_files)
    write_json_once(
        run.root / "input-manifest.json",
        {
            "schema_version": 1,
            "request": request,
            "source_snapshot": source_before,
            "contract": file_identity(contract_path),
            "worker": file_identity(worker),
            "source_policy": {
                "mode": "read_only",
                "artifact_contract": "$CWD/.aivw",
                "rebind_location": "$RUN/work",
                "source_maestro_must_remain_unchanged": True,
            },
        },
    )

    setup_modules: tuple[str, ...] = ()
    tools: tuple[ToolResult, ...] = ()
    environment: dict[str, str] = {}
    process: ProcessResult | None = None
    canonical: dict[str, object] = {}
    evidence: dict[str, object] = {}
    maestro: dict[str, object] = {}
    historical: dict[str, object] = {}
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
        dbaccess = next((item.path for item in tools if item.name == "dbaccess"), "")
        if not dbaccess:
            raise EnvironmentError("qualified toolchain has no dbAccess")
        child = dict(environment)
        child.update(
            {
                "PROJECT": str(adc_root),
                "CDS_SITE": str(adc_root / "setup" / "site"),
                "CDS_DB_TYPE": "oa",
                "CDS_AUTO_64BIT": "ALL",
                "PWD": str(work),
                "AIVW_M1_OUTPUT": str(work / "oa-evidence.json"),
            }
        )
        process = (process_runner or run_process_group)(
            (dbaccess, "-cdslib", str(cds_lib), "-load", str(worker)),
            cwd=work,
            environment=child,
            log_file=logs / "dbaccess-console.log",
            timeout=timeout,
        )
        if process.timed_out:
            status = "TIMEOUT"
            raise EnvironmentError("M1 dbAccess qualification timed out")
        if process.returncode != 0:
            status = "FAIL_DBACCESS"
            raise EnvironmentError(f"M1 dbAccess qualification exited with {process.returncode}")
        catalog = _load_oa_evidence(work / "oa-evidence.json")
        maestro = parse_maestro(maestro_root / "maestro.sdb")
        historical = parse_history_rdb(history)
        canonical, evidence = qualify_inputs(
            contract,
            maestro,
            catalog,
            historical,
            manual_sha256=sha256_file(manual),
        )
        status = "PASS" if evidence["all_checks_pass"] else "BLOCKED_INPUT"
        write_json_once(run.root / "canonical-comparator-inputs.json", canonical)
        write_json_once(run.root / "historical-reference.json", historical)
    except _DbAccessArtifactError as exc:
        status, error = "FAIL_DBACCESS_ARTIFACT", str(exc)
    except (EnvironmentError, OSError, ValueError, json.JSONDecodeError, ET.ParseError) as exc:
        error = str(exc)

    source_after = _snapshot(source_files)
    sources_unchanged = source_before == source_after
    if not sources_unchanged:
        status = "BLOCKED_ACTIVE_SOURCE"
        error = "one or more M1 source files changed during qualification"
    manifest: dict[str, object] = {
        "schema_version": 1,
        "product": {"name": PRODUCT_NAME, "command": COMMAND_NAME, "version": __version__},
        "kind": "m1-ai-input-qualification",
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
        "process": process.to_dict() if process else None,
        "maestro": maestro,
        "historical_reference": historical,
        "canonical_inputs": canonical,
        "verdict_evidence": evidence,
        "source_snapshot_before": source_before,
        "source_snapshot_after": source_after,
        "sources_unchanged": sources_unchanged,
        "next_gate": "isolated comparator-to-comparator_new rebinding and Spectre rerun",
        "does_not_prove": contract["qualification_policy"]["qualification_does_not_prove"],
        "error": error,
    }
    manifest["artifacts"] = _artifact_index(run.root, {run.manifest})
    write_json_once(run.manifest, manifest)
    return manifest


__all__ = ["run_m1_ai_qualification"]
