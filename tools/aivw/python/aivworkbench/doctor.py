"""M0 toolchain and input qualification with a durable manifest."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Sequence

from . import COMMAND_NAME, PRODUCT_NAME, __version__
from .errors import EnvironmentError, WorkspaceError
from .profiles import Profile, ToolSpec, product_root
from .toolchain import (
    ToolResult,
    capture_module_environment,
    parse_setup_exports,
    parse_setup_modules,
    probe_tools,
    safe_environment_summary,
)
from .workspace import (
    LaunchPaths,
    allocate_run,
    resolve_project_db_root,
    sha256_file,
    stable_digest,
    write_json_once,
)


EnvironmentLoader = Callable[[Sequence[str]], dict[str, str]]
ToolProber = Callable[[Sequence[ToolSpec], Mapping[str, str]], tuple[ToolResult, ...]]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _input_records(profile: Profile) -> tuple[list[dict[str, object]], bool]:
    records: list[dict[str, object]] = []
    complete = True
    for item in profile.inputs:
        exists = item.path.is_file() if item.kind == "file" else item.path.is_dir()
        record: dict[str, object] = {
            "name": item.name,
            "path": str(item.path),
            "kind": item.kind,
            "exists": exists,
        }
        if exists and item.kind == "file":
            record["size"] = item.path.stat().st_size
            record["sha256"] = sha256_file(item.path)
        else:
            complete = complete and exists
        records.append(record)
    return records, complete


def _project_records(profile: Profile) -> tuple[list[dict[str, object]], bool]:
    records: list[dict[str, object]] = []
    complete = True
    for name, project in profile.projects.items():
        root_text = project.get("root")
        root = Path(str(root_text)) if isinstance(root_text, str) else None
        cds_lib_text = project.get("cds_lib")
        cds_lib = Path(str(cds_lib_text)) if isinstance(cds_lib_text, str) else None
        environment = project.get("environment", {})
        environment_paths = (
            {
                str(variable): {
                    "path": str(value),
                    "exists": Path(str(value)).is_dir(),
                }
                for variable, value in environment.items()
            }
            if isinstance(environment, Mapping)
            else {}
        )
        record: dict[str, object] = {
            "name": str(name),
            "library": project.get("library"),
            "root": str(root) if root else None,
            "root_exists": root.is_dir() if root else False,
            "cds_lib": str(cds_lib) if cds_lib else None,
            "cds_lib_exists": cds_lib.is_file() if cds_lib else None,
            "environment": environment_paths,
        }
        if cds_lib is not None and cds_lib.is_file():
            record["cds_lib_sha256"] = sha256_file(cds_lib)
        project_complete = bool(root and root.is_dir()) and all(
            item["exists"] for item in environment_paths.values()
        )
        if project.get("library") is not None:
            project_complete = project_complete and bool(cds_lib and cds_lib.is_file())
        record["complete"] = project_complete
        complete = complete and project_complete
        records.append(record)
    return records, complete


def run_doctor(
    profile: Profile,
    launch: LaunchPaths,
    *,
    environment_loader: EnvironmentLoader | None = None,
    tool_prober: ToolProber | None = None,
) -> dict[str, object]:
    started = _now()
    setup_exists = profile.setup_script.is_file()
    setup_hash = sha256_file(profile.setup_script) if setup_exists else ""
    setup_modules: tuple[str, ...] = ()
    setup_error = ""
    if setup_exists:
        try:
            setup_modules = parse_setup_modules(profile.setup_script)
        except EnvironmentError as exc:
            setup_error = str(exc)
    else:
        setup_error = f"setup script is missing: {profile.setup_script}"
    module_match = setup_modules == profile.modules
    inputs, inputs_complete = _input_records(profile)
    projects, projects_complete = _project_records(profile)

    payload_root = None
    storage_error = ""
    if profile.storage and setup_exists:
        try:
            exports = parse_setup_exports(
                profile.setup_script, (profile.storage.payload_env,)
            )
            payload_root = resolve_project_db_root(
                exports[profile.storage.payload_env],
                launch,
                forbidden_roots=(product_root(),),
            )
        except (EnvironmentError, WorkspaceError, OSError) as exc:
            storage_error = str(exc)

    environment: dict[str, str] = {}
    environment_error = ""
    tools: tuple[ToolResult, ...] = ()
    if not setup_error and module_match:
        try:
            loader = environment_loader or (
                lambda values: capture_module_environment(values)
            )
            environment = loader(profile.modules)
            prober = tool_prober or probe_tools
            tools = prober(profile.tools, environment)
        except EnvironmentError as exc:
            environment_error = str(exc)
    toolchain_complete = bool(tools) and all(item.status == "PASS" for item in tools)

    if not inputs_complete or not projects_complete:
        status = "BLOCKED_INPUT"
    elif (
        setup_error
        or storage_error
        or not module_match
        or environment_error
        or not toolchain_complete
    ):
        status = "BLOCKED_ENVIRONMENT"
    else:
        status = "PASS"

    request = {
        "profile": profile.name,
        "profile_sha256": sha256_file(profile.source_path),
        "setup_sha256": setup_hash,
        "modules": profile.modules,
        "logical_cwd": str(launch.logical_cwd),
        "kind": "doctor",
        "payload_root": str(payload_root) if payload_root else "",
    }
    request_digest = stable_digest(request)
    run = allocate_run(launch, "doctor", request_digest)
    manifest: dict[str, object] = {
        "schema_version": 1,
        "product": {
            "name": PRODUCT_NAME,
            "command": COMMAND_NAME,
            "version": __version__,
        },
        "kind": "toolchain_doctor",
        "status": status,
        "started_at": started,
        "finished_at": _now(),
        "request_digest": request_digest,
        "run_id": run.run_id,
        "run_dir": str(run.root),
        "launch": {
            "logical_cwd": str(launch.logical_cwd),
            "physical_cwd": str(launch.physical_cwd),
            "logical_artifact_root": str(launch.logical_artifact_root),
            "physical_artifact_root": str(launch.physical_artifact_root),
        },
        "profile": {
            "name": profile.name,
            "display_name": profile.display_name,
            "path": str(profile.source_path),
            "sha256": request["profile_sha256"],
            "workspace_root": str(profile.workspace_root),
        },
        "setup": {
            "path": str(profile.setup_script),
            "exists": setup_exists,
            "sha256": setup_hash,
            "declared_modules": list(setup_modules),
            "expected_modules": list(profile.modules),
            "match": module_match,
            "error": setup_error,
        },
        "environment": (
            safe_environment_summary(environment)
            if environment
            else {"selected": {}, "mps_detached": True, "error": environment_error}
        ),
        "storage": {
            "control_root": str(launch.logical_artifact_root),
            "payload_env": profile.storage.payload_env if profile.storage else None,
            "payload_root": str(payload_root) if payload_root else None,
            "target_layout": profile.storage.target_layout if profile.storage else None,
            "qualified": not storage_error if profile.storage else True,
            "error": storage_error,
        },
        "tools": [item.to_dict() for item in tools],
        "qualification_inputs": inputs,
        "projects": projects,
        "source_policy": {
            "pilot_sources_read_only": True,
            "approved_text_view_publish": ["systemverilog", "veriloga"],
            "excluded_workspace": "smic28",
            "control_contract": "$CWD/.aivw",
            "payload_contract": "$PROJ_AMS_DB_DIR/<lib>.<cell>.<view>/runs/<run_id>",
        },
    }
    write_json_once(run.manifest, manifest)
    return manifest
