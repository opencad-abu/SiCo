"""Generic, target-independent executor contract and DAG state machine."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from .errors import ExecutorError
from .recipe import Recipe
from .recipe_dag import workflow_dependency_order, workflow_order
from .registry import PluginRegistry
from .workspace import SplitRunPaths, write_json_once


_SUCCESS = {"PASS"}
_TERMINAL_PREFIXES = ("PASS", "FAIL_", "BLOCKED_", "SKIPPED_", "STALE_")


@dataclass(frozen=True)
class ExternalArtifactLocator:
    """Bounded reference to an EDA-produced file or directory in the payload."""

    path: Path
    producer: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutorResult:
    status: str
    summary: Mapping[str, Any] = field(default_factory=dict)
    outputs: Mapping[str, Any] = field(default_factory=dict)
    artifacts: tuple[Path, ...] = ()
    artifact_locators: tuple[ExternalArtifactLocator, ...] = ()


@dataclass(frozen=True)
class ExecutorContext:
    recipe: Recipe
    run: SplitRunPaths
    gate_id: str
    executor_name: str
    dependencies: Mapping[str, ExecutorResult]
    environment: Mapping[str, str]
    tools: Mapping[str, str]
    timeout: float
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def gate_root(self) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", self.gate_id)
        return self.run.payload_root / "checks" / "gates" / safe


@dataclass(frozen=True)
class WorkflowResult:
    status: str
    gates: Mapping[str, ExecutorResult]
    artifacts: tuple[Path, ...]
    artifact_locators: tuple[Mapping[str, Any], ...]


def execute_workflow(
    recipe: Recipe,
    registry: PluginRegistry,
    run: SplitRunPaths,
    *,
    environment: Mapping[str, str] | None = None,
    tools: Mapping[str, str] | None = None,
    timeout: float = 900.0,
    targets: Sequence[str] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> WorkflowResult:
    """Execute registered gates in dependency order and fail closed per node.

    When ``targets`` is supplied, only their complete dependency closure is
    executed.  Independent DAG branches and downstream gates are not started.
    """
    if timeout <= 0:
        raise ExecutorError("workflow timeout must be positive")
    raw_gates = recipe.payload["workflow"]["gates"]
    by_id = {str(item["id"]): item for item in raw_gates}
    results: dict[str, ExecutorResult] = {}
    artifacts: list[Path] = []
    seen_artifacts: set[Path] = set()
    artifact_locators: list[Mapping[str, Any]] = []
    seen_locators: dict[str, Mapping[str, Any]] = {}
    order = (
        workflow_order(recipe.payload["workflow"])
        if targets is None
        else workflow_dependency_order(recipe.payload["workflow"], targets)
    )
    for gate_id in order:
        gate = by_id[gate_id]
        needs = tuple(str(item) for item in gate["needs"])
        dependency_results = {name: results[name] for name in needs}
        blocked = [
            name
            for name, result in dependency_results.items()
            if result.status not in _SUCCESS
        ]
        spec = registry.require_executor(str(gate["executor"]))
        gate_environment = dict(environment or {})
        if "ipc" not in spec.capabilities:
            for name in tuple(gate_environment):
                if name.startswith(("SICO_AI_", "CAD_AI_", "CAD_CODEX_")):
                    gate_environment.pop(name, None)
        gate_metadata = dict(metadata or {})
        # Handlers may resolve a recipe-declared model class through the same
        # registry used to validate the DAG.  The registry is process-local and
        # never serialized into manifests or passed to non-IPC children.
        gate_metadata.setdefault("registry", registry)
        context = ExecutorContext(
            recipe=recipe,
            run=run,
            gate_id=gate_id,
            executor_name=spec.name,
            dependencies=MappingProxyType(dependency_results),
            environment=MappingProxyType(gate_environment),
            tools=MappingProxyType(dict(tools or {})),
            timeout=timeout,
            metadata=MappingProxyType(gate_metadata),
        )
        context.gate_root.mkdir(parents=True, exist_ok=True)
        if blocked:
            result = ExecutorResult(
                "SKIPPED_DEPENDENCY",
                {"blocked_by": blocked},
            )
        elif spec.handler is None:
            result = ExecutorResult(
                "BLOCKED_EXECUTOR_UNAVAILABLE",
                {"executor": spec.name, "version": spec.version},
            )
        else:
            try:
                raw_result = spec.handler(context)
            except Exception as exc:  # executor boundary: never publish a partial PASS
                result = ExecutorResult(
                    "BLOCKED_EXECUTOR_EXCEPTION",
                    {
                        "executor": spec.name,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                )
            else:
                if not isinstance(raw_result, ExecutorResult):
                    raise ExecutorError(
                        f"executor {spec.name} returned {type(raw_result).__name__}, expected ExecutorResult"
                    )
                result = _validate_result(raw_result, run.payload_root, spec.name)
        result_path = context.gate_root / "result.json"
        write_json_once(
            result_path,
            {
                "schema_version": 1,
                "gate_id": gate_id,
                "executor": spec.name,
                "executor_version": spec.version,
                "needs": list(needs),
                "status": result.status,
                "summary": dict(result.summary),
                "outputs": dict(result.outputs),
                "artifacts": [
                    str(path.resolve().relative_to(run.payload_root.resolve()))
                    for path in result.artifacts
                ],
                "artifact_locators": [
                    _locator_record(locator, run.payload_root)
                    for locator in result.artifact_locators
                ],
            },
        )
        result = ExecutorResult(
            result.status,
            result.summary,
            result.outputs,
            (*result.artifacts, result_path),
            result.artifact_locators,
        )
        results[gate_id] = result
        # Downstream gates may cite upstream artifacts as evidence, so index
        # each physical file once when aggregating the workflow artifact set.
        for artifact in result.artifacts:
            if artifact not in seen_artifacts:
                seen_artifacts.add(artifact)
                artifacts.append(artifact)
        for locator in result.artifact_locators:
            record = _locator_record(locator, run.payload_root)
            relative = str(record["path"])
            previous = seen_locators.get(relative)
            if previous is not None and previous != record:
                raise ExecutorError(
                    f"workflow gates returned conflicting artifact locators: {relative}"
                )
            if previous is None:
                seen_locators[relative] = record
                artifact_locators.append(record)
    statuses = [result.status for result in results.values()]
    if all(status == "PASS" for status in statuses):
        status = "PASS"
    elif any(status.startswith("FAIL_") for status in statuses):
        status = "FAIL_WORKFLOW"
    else:
        status = "BLOCKED_WORKFLOW"
    return WorkflowResult(
        status,
        MappingProxyType(results),
        tuple(artifacts),
        tuple(artifact_locators),
    )


def _validate_result(
    result: ExecutorResult, payload_root: Path, executor: str
) -> ExecutorResult:
    if not result.status or not result.status.startswith(_TERMINAL_PREFIXES):
        raise ExecutorError(
            f"executor {executor} returned invalid status: {result.status!r}"
        )
    try:
        json.dumps(dict(result.summary), allow_nan=False)
        json.dumps(dict(result.outputs), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ExecutorError(
            f"executor {executor} returned non-JSON evidence: {exc}"
        ) from exc
    physical_root = payload_root.resolve()
    normalized: list[Path] = []
    for candidate in result.artifacts:
        path = candidate if candidate.is_absolute() else payload_root / candidate
        if (
            path.is_symlink()
            or not path.is_file()
            or not path.resolve().is_relative_to(physical_root)
        ):
            raise ExecutorError(f"executor {executor} returned unsafe artifact: {path}")
        normalized.append(path.resolve())
    normalized_locators: list[ExternalArtifactLocator] = []
    seen_locators: set[Path] = set()
    for locator in result.artifact_locators:
        if not isinstance(locator, ExternalArtifactLocator):
            raise ExecutorError(
                f"executor {executor} returned invalid artifact locator type"
            )
        if not isinstance(locator.path, Path):
            raise ExecutorError(
                f"executor {executor} returned artifact locator with invalid path"
            )
        if not locator.path.is_absolute() and ".." in locator.path.parts:
            raise ExecutorError(
                f"executor {executor} returned artifact locator with traversal: {locator.path}"
            )
        if not isinstance(locator.producer, str) or not locator.producer:
            raise ExecutorError(
                f"executor {executor} returned artifact locator without producer"
            )
        if not isinstance(locator.metadata, Mapping):
            raise ExecutorError(
                f"executor {executor} returned non-object locator metadata"
            )
        try:
            json.dumps(dict(locator.metadata), allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ExecutorError(
                f"executor {executor} returned non-JSON locator metadata: {exc}"
            ) from exc
        if set(locator.metadata) & {"path", "kind", "exists", "producer", "sha256", "size"}:
            raise ExecutorError(
                f"executor {executor} returned reserved artifact locator metadata"
            )
        path = locator.path if locator.path.is_absolute() else payload_root / locator.path
        if (
            path.is_symlink()
            or not path.exists()
            or not (path.is_file() or path.is_dir())
            or not path.resolve().is_relative_to(physical_root)
            or _has_symlink_component(physical_root, path)
        ):
            raise ExecutorError(
                f"executor {executor} returned unsafe artifact locator: {path}"
            )
        resolved = path.resolve()
        if resolved in seen_locators:
            raise ExecutorError(
                f"executor {executor} returned duplicate artifact locator: {resolved}"
            )
        seen_locators.add(resolved)
        normalized_locators.append(
            ExternalArtifactLocator(resolved, locator.producer, dict(locator.metadata))
        )
    return ExecutorResult(
        result.status,
        dict(result.summary),
        dict(result.outputs),
        tuple(normalized),
        tuple(normalized_locators),
    )


def _locator_record(
    locator: ExternalArtifactLocator, payload_root: Path
) -> dict[str, Any]:
    path = locator.path.resolve()
    record: dict[str, Any] = {
        "path": path.relative_to(payload_root.resolve()).as_posix(),
        "kind": "directory" if path.is_dir() else "file",
        "exists": True,
        "producer": locator.producer,
    }
    record.update(dict(locator.metadata))
    return record


def _has_symlink_component(root: Path, candidate: Path) -> bool:
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        return True
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


__all__ = [
    "ExecutorContext",
    "ExternalArtifactLocator",
    "ExecutorResult",
    "WorkflowResult",
    "execute_workflow",
]
