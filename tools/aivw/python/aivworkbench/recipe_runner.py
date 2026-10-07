"""Target-independent recipe planning and bounded DAG execution."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from .errors import ExecutorError
from .design_ir import (
    ConfigBindingValidationError,
    DesignIRAssemblyError,
    config_binding_artifact_locator,
    build_snapshot_context,
    load_config_binding_artifact,
)
from .executor import execute_workflow
from .manifest import publish_split_manifests, verify_split_manifests
from .profiles import Profile, product_root
from .recipe import Recipe
from .recipe_dag import workflow_dependency_order
from .registry import PluginRegistry, builtin_registry
from .toolchain import parse_setup_exports, safe_environment_summary
from .workspace import (
    LaunchPaths,
    allocate_split_run,
    resolve_project_db_root,
    sha256_file,
    stable_digest,
    write_json_once,
)


def run_recipe_dry_run(
    profile: Profile,
    launch: LaunchPaths,
    recipe: Recipe,
    *,
    registry: PluginRegistry | None = None,
) -> dict[str, Any]:
    """Validate a recipe/DAG and allocate its real split-storage envelope.

    This deliberately executes no registered gate and makes no design claim.
    It is the first generic kernel path; executable handlers will be attached
    incrementally without adding block-specific dispatch here.
    """
    if profile.storage is None:
        raise ValueError(f"profile {profile.name!r} has no split-storage contract")
    active_registry = registry or builtin_registry()
    exports = parse_setup_exports(profile.setup_script, (profile.storage.payload_env,))
    project_db_root = resolve_project_db_root(
        exports[profile.storage.payload_env],
        launch,
        forbidden_roots=(product_root(),),
    )
    output = recipe.target["output_view"]
    target = {
        "library": recipe.target["library"],
        "cell": recipe.target["cell"],
        "view": output["name"],
        "language": output["language"],
    }
    gates = _gate_plan(recipe, active_registry)
    request = {
        "schema_version": 1,
        "kind": "recipe_run_request",
        "mode": "dry_run",
        "profile": {
            "name": profile.name,
            "path": str(profile.source_path),
            "sha256": sha256_file(profile.source_path),
        },
        "recipe": {
            "id": recipe.recipe_id,
            "revision": recipe.revision,
            "path": str(recipe.path),
            "sha256": recipe.sha256,
        },
        "target": target,
    }
    request_digest = stable_digest(request)
    run = allocate_split_run(
        launch,
        project_db_root,
        library=str(target["library"]),
        cell=str(target["cell"]),
        view=str(target["view"]),
        kind="recipe-dry-run",
        request_digest=request_digest,
    )
    request_path = run.control_root / "request.json"
    report_path = run.control_root / "report.json"
    write_json_once(request_path, request)
    write_json_once(
        report_path,
        {
            "schema_version": 1,
            "status": "DRY_RUN_PASS",
            "scope": "recipe-contract-dag-and-split-storage",
            "design_verdict": "NOT_RUN",
            "gates_executed": 0,
            "gates_planned": len(gates),
            "non_executable_gates": [
                item["id"] for item in gates if not item["executable"]
            ],
        },
    )
    finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    control, _payload = publish_split_manifests(
        run,
        request_digest=request_digest,
        status="DRY_RUN_PASS",
        control_details={
            "mode": "dry_run",
            "scope": "recipe-contract-dag-and-split-storage",
            "design_verdict": "NOT_RUN",
            "finished_at": finished_at,
            "profile": request["profile"],
            "recipe": request["recipe"],
            "target": target,
            "gate_plan": gates,
            "payload_root": str(run.payload_root),
        },
        payload_details={
            "mode": "dry_run",
            "scope": "storage-envelope-only",
            "design_verdict": "NOT_RUN",
            "finished_at": finished_at,
            "recipe": request["recipe"],
            "target": target,
            "artifact_policy": "explicit-index; no EDA process was started",
        },
        control_artifacts=(request_path, report_path),
    )
    result = dict(control)
    result["manifest_pair"] = verify_split_manifests(
        run.control_manifest, run.payload_manifest
    )
    return result


def run_recipe_through(
    profile: Profile,
    launch: LaunchPaths,
    recipe: Recipe,
    *,
    through: str,
    registry: PluginRegistry | None = None,
    environment: Mapping[str, str] | None = None,
    tools: Mapping[str, str] | None = None,
    tool_evidence: Sequence[Mapping[str, object]] | None = None,
    timeout: float = 900.0,
) -> dict[str, Any]:
    """Execute one gate and its dependency closure, never publication.

    The current command deliberately has no publication approval input.  Even
    if a publisher handler is registered later, this path cannot reach it.
    """
    if profile.storage is None:
        raise ValueError(f"profile {profile.name!r} has no split-storage contract")
    if timeout <= 0:
        raise ExecutorError("workflow timeout must be positive")
    active_registry = registry or builtin_registry()
    selected = workflow_dependency_order(recipe.payload["workflow"], (through,))
    by_id = {str(item["id"]): item for item in recipe.payload["workflow"]["gates"]}
    publication = [
        gate_id
        for gate_id in selected
        if by_id[gate_id]["executor"] == "virtuoso.publish_text_view"
    ]
    if publication:
        raise ExecutorError(
            "recipe publication requires a separate human-approved promotion command"
        )
    exports = parse_setup_exports(profile.setup_script, (profile.storage.payload_env,))
    project_db_root = resolve_project_db_root(
        exports[profile.storage.payload_env],
        launch,
        forbidden_roots=(product_root(),),
    )
    output = recipe.target["output_view"]
    target = {
        "library": recipe.target["library"],
        "cell": recipe.target["cell"],
        "view": output["name"],
        "language": output["language"],
    }
    structure_metadata = _structure_metadata(profile, recipe)
    qualified_tools = dict(tools or {})
    run_environment = dict(environment or {})
    request = {
        "schema_version": 1,
        "kind": "recipe_run_request",
        "mode": "dependency_closure",
        "through": through,
        "selected_gates": list(selected),
        "profile": {
            "name": profile.name,
            "path": str(profile.source_path),
            "sha256": sha256_file(profile.source_path),
        },
        "recipe": {
            "id": recipe.recipe_id,
            "revision": recipe.revision,
            "path": str(recipe.path),
            "sha256": recipe.sha256,
        },
        "target": target,
        "environment": safe_environment_summary(run_environment),
        "tools": qualified_tools,
        "tool_qualification": [dict(item) for item in (tool_evidence or ())],
        "publication_authorized": False,
    }
    request_digest = stable_digest(request)
    run = allocate_split_run(
        launch,
        project_db_root,
        library=str(target["library"]),
        cell=str(target["cell"]),
        view=str(target["view"]),
        kind="recipe-run",
        request_digest=request_digest,
    )
    request_path = run.control_root / "request.json"
    report_path = run.control_root / "report.json"
    write_json_once(request_path, request)
    workflow = execute_workflow(
        recipe,
        active_registry,
        run,
        environment=run_environment,
        tools=qualified_tools,
        timeout=timeout,
        targets=(through,),
        metadata=structure_metadata,
    )
    gate_results = {
        gate_id: {
            "executor": by_id[gate_id]["executor"],
            "status": result.status,
            "summary": dict(result.summary),
            "outputs": dict(result.outputs),
            "artifacts": [
                path.relative_to(run.payload_root.resolve()).as_posix()
                for path in result.artifacts
            ],
            "artifact_locators": [
                {
                    "path": locator.path.relative_to(run.payload_root.resolve()).as_posix(),
                    "kind": "directory" if locator.path.is_dir() else "file",
                    "exists": True,
                    "producer": locator.producer,
                    **dict(locator.metadata),
                }
                for locator in result.artifact_locators
            ],
        }
        for gate_id, result in workflow.gates.items()
    }
    agent_context, agent_context_status = _assemble_agent_context(
        gate_results,
        run.payload_root,
        target={
            "library": recipe.target["library"],
            "cell": recipe.target["cell"],
            "module": recipe.target["module"],
            "config_view": recipe.target.get("source_views", {}).get("config")
            if isinstance(recipe.target.get("source_views"), Mapping)
            else None,
        },
    )
    write_json_once(
        report_path,
        {
            "schema_version": 1,
            "status": workflow.status,
            "scope": "selected-gate-dependency-closure",
            "through": through,
            "selected_gates": list(selected),
            "design_verdict": "SELECTED_GATES_PASS"
            if workflow.status == "PASS"
            else "NOT_ESTABLISHED",
            "publication": "NOT_AUTHORIZED",
            "agent_context": agent_context,
            "agent_context_status": agent_context_status,
            "gates": gate_results,
        },
    )
    finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    control, _payload = publish_split_manifests(
        run,
        request_digest=request_digest,
        status=workflow.status,
        control_details={
            "mode": "dependency_closure",
            "scope": "selected-gate-dependency-closure",
            "through": through,
            "selected_gates": list(selected),
            "design_verdict": "SELECTED_GATES_PASS"
            if workflow.status == "PASS"
            else "NOT_ESTABLISHED",
            "publication": "NOT_AUTHORIZED",
            "finished_at": finished_at,
            "profile": request["profile"],
            "recipe": request["recipe"],
            "target": target,
            "gate_results": gate_results,
            "agent_context": agent_context,
            "agent_context_status": agent_context_status,
            "payload_root": str(run.payload_root),
        },
        payload_details={
            "mode": "dependency_closure",
            "through": through,
            "selected_gates": list(selected),
            "finished_at": finished_at,
            "recipe": request["recipe"],
            "target": target,
            "artifact_policy": "executor-declared artifacts and per-gate result records indexed",
        },
        control_artifacts=(request_path, report_path),
        payload_artifacts=workflow.artifacts,
        payload_locators=workflow.artifact_locators,
    )
    result = dict(control)
    result["manifest_pair"] = verify_split_manifests(
        run.control_manifest, run.payload_manifest
    )
    return result


def _gate_plan(recipe: Recipe, registry: PluginRegistry) -> list[dict[str, object]]:
    gates = recipe.payload["workflow"]["gates"]
    return [
        {
            "id": gate["id"],
            "executor": gate["executor"],
            "executor_version": registry.require_executor(gate["executor"]).version,
            "needs": list(gate["needs"]),
            "executable": registry.require_executor(gate["executor"]).handler
            is not None,
            "state": "NOT_RUN",
        }
        for gate in gates
    ]


def _assemble_agent_context(
    gate_results: Mapping[str, Mapping[str, Any]],
    payload_root: object,
    *,
    target: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, str]:
    """Build context only from the current run's authenticated snapshot gate."""

    snapshots = [
        gate
        for gate in gate_results.values()
        if gate.get("executor") == "virtuoso.snapshot" and gate.get("status") == "PASS"
    ]
    if len(snapshots) != 1:
        return None, "BLOCKED_CONTEXT_SNAPSHOT_UNAVAILABLE"
    config_gates = [
        gate
        for gate in gate_results.values()
        if gate.get("executor") == "virtuoso.config_binding"
    ]
    if len(config_gates) > 1:
        return {"error": "multiple PASS config binding gates", "code": "ambiguous_config_binding"}, "BLOCKED_CONTEXT_ASSEMBLY"
    if config_gates and config_gates[0].get("status") != "PASS":
        return {
            "error": "config binding gate did not establish a PASS artifact",
            "code": "config_binding_unavailable",
        }, "BLOCKED_CONTEXT_ASSEMBLY"
    config_view = target.get("config_view")
    if isinstance(config_view, str) and config_view and not config_gates:
        return {
            "error": "recipe selected a config view without a config binding gate",
            "code": "config_binding_unavailable",
        }, "BLOCKED_CONTEXT_ASSEMBLY"
    config_binding = None
    config_binding_locator = None
    try:
        snapshot_outputs = snapshots[0].get("outputs")
        source_generation = (
            snapshot_outputs.get("source_generation")
            if isinstance(snapshot_outputs, Mapping)
            else None
        )
        snapshot_target = (
            snapshot_outputs.get("target")
            if isinstance(snapshot_outputs, Mapping)
            else None
        )
        if not isinstance(source_generation, str) or not isinstance(snapshot_target, Mapping):
            raise DesignIRAssemblyError("snapshot outputs cannot bind config artifact")
        if any(
            snapshot_target.get(key) != target.get(key)
            for key in ("library", "cell", "module")
        ):
            raise DesignIRAssemblyError("snapshot target does not match recipe target")
        if config_gates:
            config_binding_locator = config_binding_artifact_locator(
                config_gates[0], payload_root
            )
            config_binding = load_config_binding_artifact(
                config_gates[0],
                payload_root,
                target={**snapshot_target, "config_view": config_view},
                source_generation=source_generation,
            ).to_dict()
        context = build_snapshot_context(
            snapshots[0],
            payload_root,
            config_binding=config_binding,
            config_binding_locator=config_binding_locator,
        )
    except (ConfigBindingValidationError, DesignIRAssemblyError, OSError, ValueError) as exc:
        return {"error": str(exc), "code": "design_ir_assembly_failed"}, "BLOCKED_CONTEXT_ASSEMBLY"
    return context.to_dict(), "PASS"


def _structure_metadata(profile: Profile, recipe: Recipe) -> dict[str, object]:
    """Resolve project-owned structural inputs without target-specific kernel logic."""
    library = str(recipe.target["library"])
    matches: list[tuple[str, Mapping[str, object]]] = []
    for project_name, raw_project in profile.projects.items():
        if not isinstance(raw_project, Mapping):
            continue
        if str(raw_project.get("library", "")) == library:
            matches.append((str(project_name), raw_project))
    if len(matches) > 1:
        raise ExecutorError(
            f"profile maps library {library!r} to multiple projects: {[name for name, _ in matches]}"
        )
    if not matches:
        return {
            "project": None,
            "project_root": None,
            "workspace_root": str(profile.workspace_root),
            "cds_lib": None,
            "project_environment": {},
        }
    project_name, project = matches[0]
    cds_lib = project.get("cds_lib")
    return {
        "read_only_structure_adapter": project.get("read_only_structure_adapter"),
        "project_config": dict(project),
        "project": project_name,
        "project_root": str(project.get("root"))
        if isinstance(project.get("root"), str)
        else None,
        "workspace_root": str(profile.workspace_root),
        "cds_lib": str(cds_lib) if isinstance(cds_lib, str) else None,
        "project_environment": dict(project.get("environment", {}))
        if isinstance(project.get("environment"), Mapping)
        else {},
    }


__all__ = ["run_recipe_dry_run", "run_recipe_through"]
