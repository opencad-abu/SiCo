"""Xcelium process, generation output, and safe payload path helpers."""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
from typing import Any, Mapping

from ..artifact_paths import PathContractError, path_has_symlink_component, validate_relative_path
from ..executor import ExecutorContext, ExecutorResult

def _xrun_version(*logs: str) -> str | None:
    versions = {
        match.group(1)
        for value in logs
        for match in re.finditer(
            r"TOOL:\s*xrun(?:\(64\))?\s+([^:\r\n]+)", value
        )
    }
    return next(iter(versions)) if len(versions) == 1 else None

def _generation_outputs(dependencies: Mapping[str, ExecutorResult]) -> Mapping[str, Any]:
    candidates = [result.outputs for result in dependencies.values() if "model_source" in result.outputs]
    if len(candidates) != 1:
        raise ValueError(f"RNM check requires exactly one generation output, found {len(candidates)}")
    return candidates[0]

def _payload_file(context: ExecutorContext, values: Mapping[str, Any], key: str) -> Path:
    raw = values.get(key)
    if not isinstance(raw, str) or not raw:
        raise ValueError(f"generation output is missing {key}")
    try:
        relative = Path(validate_relative_path(raw, f"generation output {key}"))
    except PathContractError as exc:
        raise ValueError(f"generation output {key} is unsafe") from exc
    candidate = context.run.payload_root / relative
    path = candidate.resolve()
    if path_has_symlink_component(context.run.payload_root, candidate) or not path.is_file() or not path.is_relative_to(
        context.run.payload_root.resolve()
    ):
        raise ValueError(f"generation output {key} is missing or outside payload")
    return path

def _run(
    command: tuple[str, ...],
    cwd: Path,
    context: ExecutorContext,
    log: Path,
) -> dict[str, Any]:
    environment = dict(context.environment) or dict(os.environ)
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=context.timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout if isinstance(exc.stdout, str) else ""
        log.write_text(output, encoding="utf-8")
        return {
            "command": list(command),
            "returncode": None,
            "timed_out": True,
        }
    log.write_text(completed.stdout, encoding="utf-8")
    return {
        "command": list(command),
        "returncode": completed.returncode,
        "timed_out": False,
    }
