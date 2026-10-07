"""Cadence SI project environment policy and child process control."""

from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
from typing import Any, Mapping

import re

_ENVIRONMENT_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
_FORBIDDEN_ENVIRONMENT = {"HOME", "PATH", "LD_PRELOAD", "LD_LIBRARY_PATH", "PYTHONPATH", "SICO_AI_TOKEN", "CAD_AI_TOKEN", "CAD_CODEX_TOKEN"}

def _project_environment(
    metadata: Mapping[str, Any], workspace_root: Path
) -> dict[str, str]:
    raw_environment = metadata.get("project_environment", {})
    if not isinstance(raw_environment, Mapping):
        raise ValueError("project environment metadata must be an object")
    environment: dict[str, str] = {}
    for raw_name, raw_value in raw_environment.items():
        name = str(raw_name)
        if not _ENVIRONMENT_NAME.fullmatch(name) or name in _FORBIDDEN_ENVIRONMENT:
            raise ValueError(f"project environment name is forbidden: {name!r}")
        if not isinstance(raw_value, str) or not Path(raw_value).is_absolute():
            raise ValueError(
                f"project environment value must be an absolute path: {name}"
            )
        value = Path(raw_value).resolve(strict=False)
        if "smic28" in str(value).casefold() or not value.is_relative_to(
            workspace_root
        ):
            raise ValueError(
                f"project environment path escaped the approved workspace: {name}"
            )
        if not value.is_dir():
            raise ValueError(f"project environment directory is unavailable: {name}")
        environment[name] = str(value)
    return environment

def _run_process(
    command: tuple[str, ...],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    timeout: float,
) -> tuple[dict[str, object], str]:
    try:
        child = subprocess.Popen(
            command,
            cwd=cwd,
            env=dict(environment),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            start_new_session=True,
        )
    except OSError as exc:
        return (
            {"returncode": None, "timed_out": False, "error": str(exc)},
            f"{type(exc).__name__}: {exc}\n",
        )
    try:
        output, _stderr = child.communicate(timeout=timeout)
        return {"returncode": child.returncode, "timed_out": False}, output
    except subprocess.TimeoutExpired as exc:
        partial = exc.stdout if isinstance(exc.stdout, str) else ""
        _kill_process_group(child.pid, signal.SIGTERM)
        try:
            remainder, _stderr = child.communicate(timeout=5.0)
            termination = "SIGTERM"
        except subprocess.TimeoutExpired as second:
            if isinstance(second.stdout, str) and not partial:
                partial = second.stdout
            _kill_process_group(child.pid, signal.SIGKILL)
            remainder, _stderr = child.communicate()
            termination = "SIGKILL"
        output = remainder if remainder.startswith(partial) else partial + remainder
        return {
            "returncode": None,
            "timed_out": True,
            "process_group_termination": termination,
        }, output

def _kill_process_group(process_group: int, action: signal.Signals) -> None:
    try:
        os.killpg(process_group, action)
    except ProcessLookupError:
        pass
