"""M0-E Lab6 known-good execution qualification."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
import shutil
from typing import Callable, Mapping, Sequence

from . import COMMAND_NAME, PRODUCT_NAME, __version__
from .errors import EnvironmentError, ProfileError
from .process import ProcessResult, run_process_group
from .profiles import Profile, ToolSpec
from .toolchain import (
    ToolResult,
    capture_module_environment,
    parse_setup_modules,
    probe_tools,
    safe_environment_summary,
)
from .workspace import (
    LaunchPaths,
    allocate_run,
    sha256_file,
    stable_digest,
    write_json_once,
)


EnvironmentLoader = Callable[[Sequence[str]], dict[str, str]]
ToolProber = Callable[[Sequence[ToolSpec], Mapping[str, str]], tuple[ToolResult, ...]]
ProcessRunner = Callable[..., ProcessResult]
_UVM_ERROR = re.compile(r"^\s*UVM_ERROR\s*:\s*(\d+)\s*$", re.MULTILINE)
_UVM_FATAL = re.compile(r"^\s*UVM_FATAL\s*:\s*(\d+)\s*$", re.MULTILINE)
_TOOL_ERROR = re.compile(
    r"^\s*[A-Za-z0-9_.-]+:\s+\*E,([A-Z0-9]+)",
    re.MULTILINE,
)
_BENIGN_TERMINAL_ERRORS = frozenset({"RNFNSH"})
_RUNTIME_DATA_FILES = (
    "divInit.dat",
    "divInit_neg.dat",
    "divInit_fra.dat",
    "divInit_lt2.dat",
)


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


def _m0e_input(profile: Profile) -> Path:
    item = next((value for value in profile.inputs if value.name == "m0_e_run_file"), None)
    if item is None:
        raise ProfileError("profile has no m0_e_run_file qualification input")
    return item.path


def _snapshot_files(paths: Sequence[Path]) -> dict[str, dict[str, object]]:
    snapshot: dict[str, dict[str, object]] = {}
    for path in paths:
        if not path.is_file():
            continue
        stat = path.stat()
        snapshot[str(path)] = {
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "sha256": sha256_file(path),
        }
    return snapshot


def _stage_runtime_inputs(source_dir: Path, work: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for name in _RUNTIME_DATA_FILES:
        source = source_dir / name
        if not source.is_file():
            raise EnvironmentError(f"required Lab6 runtime input is missing: {source}")
        destination = work / name
        shutil.copy2(source, destination)
        source_hash = sha256_file(source)
        destination_hash = sha256_file(destination)
        if source_hash != destination_hash:
            raise EnvironmentError(f"staged Lab6 runtime input hash mismatch: {source}")
        records.append(
            {
                "name": name,
                "source": str(source),
                "destination": str(destination),
                "size": source.stat().st_size,
                "sha256": source_hash,
            }
        )
    return records


def _verdict(log_text: str, process: ProcessResult) -> tuple[str, dict[str, object]]:
    error_counts = tuple(int(value) for value in _UVM_ERROR.findall(log_text))
    fatal_counts = tuple(int(value) for value in _UVM_FATAL.findall(log_text))
    tool_error_matches = tuple(_TOOL_ERROR.finditer(log_text))
    tool_error_codes = tuple(dict.fromkeys(match.group(1) for match in tool_error_matches))
    unexpected_tool_error_codes = tuple(
        code for code in tool_error_codes if code not in _BENIGN_TERMINAL_ERRORS
    )
    finish_position = log_text.rfind("Simulation complete via $finish")
    rnfnsh_matches = tuple(
        match for match in tool_error_matches if match.group(1) == "RNFNSH"
    )
    rnfnsh_after_finish = bool(rnfnsh_matches) and finish_position >= 0 and all(
        match.start() > finish_position for match in rnfnsh_matches
    )
    evidence = {
        "uvm_error_counts": list(error_counts),
        "uvm_fatal_counts": list(fatal_counts),
        "test_passed_marker": "** TEST PASSED **" in log_text,
        "simulation_complete_marker": "Simulation complete" in log_text,
        "tool_error_codes": list(tool_error_codes),
        "unexpected_tool_error_codes": list(unexpected_tool_error_codes),
        "accepted_post_finish_rnfnsh": False,
    }
    if process.timed_out:
        return "TIMEOUT", evidence
    lowered = log_text.lower()
    if any(token in lowered for token in ("license checkout failed", "no valid license")):
        return "BLOCKED_LICENSE", evidence
    if "xmvlog: *e" in lowered or "xmvhdl: *e" in lowered:
        return "FAIL_COMPILE", evidence
    if "xmelab: *e" in lowered:
        return "FAIL_ELABORATION", evidence
    if "spectre terminated prematurely" in lowered:
        return "FAIL_AMS", evidence
    if unexpected_tool_error_codes:
        return "FAIL_SIMULATION", evidence
    if not error_counts or not fatal_counts:
        return "INCONCLUSIVE", evidence
    if any(error_counts) or any(fatal_counts):
        return "FAIL_UVM", evidence
    if not evidence["test_passed_marker"] or not evidence["simulation_complete_marker"]:
        return "INCONCLUSIVE", evidence
    if process.returncode == 0:
        return "PASS", evidence
    if (
        process.returncode == 1
        and tool_error_codes == ("RNFNSH",)
        and rnfnsh_after_finish
    ):
        evidence["accepted_post_finish_rnfnsh"] = True
        return "PASS", evidence
    return "FAIL_SIMULATION", evidence


def _artifact_index(root: Path, excluded: set[Path]) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path in excluded:
            continue
        records.append(
            {
                "path": path.relative_to(root).as_posix(),
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return records


def run_m0_e(
    profile: Profile,
    launch: LaunchPaths,
    *,
    timeout: float = 900.0,
    environment_loader: EnvironmentLoader | None = None,
    tool_prober: ToolProber | None = None,
    process_runner: ProcessRunner | None = None,
) -> dict[str, object]:
    run_file = _m0e_input(profile)
    project_root = _project_path(profile, "dms_ams_flow", "root") / "Lab6"
    test_root = project_root / "test" / "CORE_UVM"
    tracked_sources = (
        run_file,
        run_file.parent / "cdsproc_macros.svh",
        run_file.parent / "cdsproc_package.sv",
        run_file.parent / "upf_package.sv",
        run_file.parent / "LDO_MASTER.sv",
        project_root / "src" / "design.f",
        project_root / "src" / "ams" / "control_files" / "CORE_UVM_amscf.scs",
        test_root / "tb" / "TB_CORE.upf",
        *(run_file.parent / name for name in _RUNTIME_DATA_FILES),
    )
    input_snapshot = _snapshot_files(tracked_sources)
    request = {
        "pilot": "m0-e",
        "profile": profile.name,
        "profile_sha256": sha256_file(profile.source_path),
        "run_file": str(run_file),
        "run_file_sha256": sha256_file(run_file),
        "logical_cwd": str(launch.logical_cwd),
    }
    run = allocate_run(launch, "m0-e", stable_digest(request))
    work = run.root / "work"
    logs = run.root / "logs"
    work.mkdir()
    logs.mkdir()
    staged_runtime_inputs = _stage_runtime_inputs(run_file.parent, work)
    input_manifest = run.root / "input-manifest.json"
    write_json_once(
        input_manifest,
        {
            "schema_version": 1,
            "request": request,
            "source_snapshot": input_snapshot,
            "source_policy": "read_only",
            "staged_runtime_inputs": staged_runtime_inputs,
        },
    )

    setup_error = ""
    setup_modules: tuple[str, ...] = ()
    tools: tuple[ToolResult, ...] = ()
    environment: dict[str, str] = {}
    process: ProcessResult | None = None
    evidence: dict[str, object] = {}
    status = "BLOCKED_ENVIRONMENT"
    try:
        setup_modules = parse_setup_modules(profile.setup_script)
        if setup_modules != profile.modules:
            raise EnvironmentError(
                f"setup modules differ from profile: setup={setup_modules!r} profile={profile.modules!r}"
            )
        loader = environment_loader or (lambda values: capture_module_environment(values))
        environment = loader(profile.modules)
        prober = tool_prober or probe_tools
        tools = prober(profile.tools, environment)
        if not tools or any(item.status != "PASS" for item in tools):
            raise EnvironmentError("one or more EDA tools failed profile qualification")
        xrun = next((item.path for item in tools if item.name == "xrun"), "")
        if not xrun:
            raise EnvironmentError("qualified toolchain has no xrun")
        child = dict(environment)
        child.update(
            {
                "PROJDIR": str(project_root),
                "TESTDIR": str(test_root),
                "PWD": str(work),
            }
        )
        # The RAK historically runs from CORE_UVM/sim and therefore relies on
        # xmvlog's implicit current-directory include search for
        # cdsproc_macros.svh.  M0-E runs in an artifact scratch directory, so
        # make that dependency explicit without editing or writing the RAK.
        command = (
            xrun,
            "-incdir",
            str(run_file.parent),
            "-f",
            str(run_file),
            "+UVM_TESTNAME=jtag_alu_test",
            "-exit",
        )
        runner = process_runner or run_process_group
        process = runner(
            command,
            cwd=work,
            environment=child,
            log_file=logs / "xrun-console.log",
            timeout=timeout,
        )
        text = (logs / "xrun-console.log").read_text(encoding="utf-8", errors="replace")
        status, evidence = _verdict(text, process)
    except (EnvironmentError, OSError, ValueError) as exc:
        setup_error = str(exc)

    output_snapshot = _snapshot_files(tracked_sources)
    sources_unchanged = input_snapshot == output_snapshot
    if not sources_unchanged and status == "PASS":
        status = "FAIL_SOURCE_MUTATION"
    artifacts = _artifact_index(run.root, {run.manifest})
    manifest: dict[str, object] = {
        "schema_version": 1,
        "product": {"name": PRODUCT_NAME, "command": COMMAND_NAME, "version": __version__},
        "kind": "m0-e",
        "status": status,
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
        "process": None if process is None else process.to_dict(),
        "verdict_evidence": evidence,
        "source_snapshot_before": input_snapshot,
        "source_snapshot_after": output_snapshot,
        "sources_unchanged": sources_unchanged,
        "staged_runtime_inputs": staged_runtime_inputs,
        "error": setup_error,
        "artifacts": artifacts,
    }
    write_json_once(run.manifest, manifest)
    return manifest
