"""Manifest model and validation for CAD multi-cell batches."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sicostate import absolute
from sicotemp import selected_state
from .compat import tomllib


@dataclass(frozen=True)
class TaskSpec:
    index: int
    task_id: str
    label: str
    command: str
    cancel_command: str
    run_dir: Path
    config: Path
    launch_log: Path
    result_path: str
    publication_required: bool


@dataclass(frozen=True)
class BatchManifest:
    path: Path
    schema_version: int
    batch_id: str
    flow: str
    parallel_cells: int
    temp_dir: Path
    status_json: Path
    status_tsv: Path
    tasks: tuple[TaskSpec, ...]


def _required_text(values: dict[str, Any], name: str, context: str) -> str:
    value = str(values.get(name, "")).strip()
    if not value:
        raise ValueError(f"Missing {context}.{name}")
    return value


def _manifest_path(value: str, base: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def load_manifest(path: str | Path) -> BatchManifest:
    manifest_path = Path(path).expanduser().resolve()
    with manifest_path.open("rb") as stream:
        raw = tomllib.load(stream)
    batch = raw.get("batch")
    if not isinstance(batch, dict):
        raise ValueError("Missing [batch] section")
    schema_version = int(batch.get("schema_version", 0))
    if schema_version != 1:
        raise ValueError(f"Unsupported batch schema version: {schema_version}")
    flow = _required_text(batch, "flow", "batch").upper()
    if flow not in {"DRC", "LVS", "RCE"}:
        raise ValueError(f"Unsupported batch flow: {flow}")
    parallel_cells = int(batch.get("parallel_cells", 0))
    if not 1 <= parallel_cells <= 128:
        raise ValueError("batch.parallel_cells must be between 1 and 128")
    base = manifest_path.parent
    configured_temp = batch.get("temp_dir")
    temporary = None
    if configured_temp is not None:
        if not isinstance(configured_temp, str) or not configured_temp.strip():
            raise ValueError("batch.temp_dir must be a nonempty project state directory")
        temporary = Path(configured_temp).expanduser()
        if not temporary.is_absolute():
            temporary = base / temporary
        temporary = absolute(temporary)
    temp_dir = selected_state(temporary=temporary)
    status_json = _manifest_path(
        _required_text(batch, "status_json", "batch"), base
    )
    status_tsv = _manifest_path(
        _required_text(batch, "status_tsv", "batch"), base
    )
    raw_tasks = raw.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise ValueError("Add at least one [[tasks]] entry")

    tasks: list[TaskSpec] = []
    ids: set[str] = set()
    run_dirs: set[Path] = set()
    configs: set[Path] = set()
    for index, values in enumerate(raw_tasks, start=1):
        if not isinstance(values, dict):
            raise ValueError(f"tasks[{index}] must be a table")
        context = f"tasks[{index}]"
        task_id = _required_text(values, "id", context)
        if task_id in ids:
            raise ValueError(f"Duplicate task id: {task_id}")
        ids.add(task_id)
        run_dir = _manifest_path(_required_text(values, "run_dir", context), base)
        config = _manifest_path(_required_text(values, "config", context), base)
        launch_log = _manifest_path(
            _required_text(values, "launch_log", context), base
        )
        if run_dir in run_dirs:
            raise ValueError(f"Duplicate task run directory: {run_dir}")
        if config in configs:
            raise ValueError(f"Duplicate task config: {config}")
        run_dirs.add(run_dir)
        configs.add(config)
        tasks.append(
            TaskSpec(
                index=index,
                task_id=task_id,
                label=_required_text(values, "label", context),
                command=_required_text(values, "command", context),
                cancel_command=str(values.get("cancel_command", "")).strip(),
                run_dir=run_dir,
                config=config,
                launch_log=launch_log,
                result_path=str(values.get("result_path", "")).strip(),
                publication_required=bool(values.get("publication_required", False)),
            )
        )
    return BatchManifest(
        path=manifest_path,
        schema_version=schema_version,
        batch_id=_required_text(batch, "batch_id", "batch"),
        flow=flow,
        parallel_cells=parallel_cells,
        temp_dir=temp_dir,
        status_json=status_json,
        status_tsv=status_tsv,
        tasks=tuple(tasks),
    )
