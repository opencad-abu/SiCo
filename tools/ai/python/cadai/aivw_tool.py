"""Controlled MCP launcher for AI Verification Workbench recipe execution."""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from sicosessionenv import publish as publish_session

from . import process_monitor

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_.-]{0,255}$")


class AivwArgumentError(ValueError):
    pass


class AivwExecutionError(RuntimeError):
    pass


def run_aivw_recipe(
    arguments: Mapping[str, Any],
    *,
    client: Any,
    workspace: Path | None,
    base_environment: Mapping[str, str] | None = None,
    entry: Path | None = None,
    cancel_event: Any | None = None,
) -> dict[str, Any]:
    """Run one bounded recipe dependency closure with ephemeral IPC delegation."""
    allowed = {"reference", "through", "profile", "timeout"}
    unknown = set(arguments) - allowed
    if unknown:
        raise AivwArgumentError(f"unknown run_aivw_recipe argument(s): {sorted(unknown)}")
    reference = _identifier(arguments.get("reference"), "reference")
    through = _identifier(arguments.get("through"), "through")
    profile = _identifier(arguments.get("profile", "amsverify"), "profile")
    timeout = arguments.get("timeout", 900.0)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise AivwArgumentError("timeout must be a number")
    timeout = float(timeout)
    if not 1.0 <= timeout <= 3600.0:
        raise AivwArgumentError("timeout must be between 1 and 3600 seconds")
    if workspace is None:
        raise AivwExecutionError("the authenticated MCP process has no delegated AIVW workspace")
    try:
        run_cwd = workspace.expanduser().resolve(strict=True)
    except OSError as exc:
        raise AivwExecutionError(f"delegated AIVW workspace is unavailable: {exc}") from exc
    if not run_cwd.is_dir():
        raise AivwExecutionError(f"delegated AIVW workspace is not a directory: {run_cwd}")
    wrapper = (
        Path(__file__).resolve().parents[3] / "aivw" / "bin" / "aivw"
        if entry is None
        else entry.expanduser().resolve()
    )
    if not wrapper.is_file() or not os.access(wrapper, os.X_OK):
        raise AivwExecutionError(f"AIVW entry is unavailable: {wrapper}")
    environment = dict(os.environ if base_environment is None else base_environment)
    publish_session(environment, RUNTIME=client.runtime.root, SOCKET=client.path,
                    SPOOL=client.runtime.spool, TOKEN=client.token, WORKSPACE=run_cwd)
    environment["PWD"] = str(run_cwd)
    command = (
        str(wrapper),
        "run-recipe",
        reference,
        "--through",
        through,
        "--profile",
        profile,
        "--timeout",
        f"{timeout:g}",
        "--json",
    )
    process = subprocess.Popen(
        command,
        cwd=run_cwd,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        start_new_session=True,
    )
    deadline = time.monotonic() + timeout + 30.0
    process_record = process_monitor.track(process, command, run_cwd, name="aivw")
    while True:
        if cancel_event is not None and cancel_event.is_set():
            _terminate_process_group(process)
            stdout, stderr = process.communicate()
            process_monitor.output(process_record, stdout, stderr)
            process_monitor.finish(process_record, process.returncode)
            raise AivwExecutionError(
                "AIVW live run was cancelled; the process group was terminated and not retried"
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _terminate_process_group(process)
            stdout, stderr = process.communicate()
            process_monitor.output(process_record, stdout, stderr)
            process_monitor.finish(process_record, process.returncode)
            raise AivwExecutionError(
                "AIVW exceeded its delegated timeout; the process group was terminated and not retried"
            )
        try:
            stdout, stderr = process.communicate(timeout=min(0.25, remaining))
            break
        except subprocess.TimeoutExpired as exc:
            process_monitor.output(process_record, exc.output, exc.stderr)
            continue
    process_monitor.output(process_record, stdout, stderr)
    process_monitor.finish(process_record, process.returncode)
    if process.returncode not in {0, 1}:
        detail = _safe_process_error(stderr, str(getattr(client, "token", "")))
        raise AivwExecutionError(
            f"AIVW exited with {process.returncode}: {detail or 'no diagnostic'}"
        )
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise AivwExecutionError(
            f"AIVW did not return JSON: {_safe_process_error(stderr, str(getattr(client, 'token', ''))) or 'no diagnostic'}"
        ) from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("status"), str):
        raise AivwExecutionError("AIVW returned an invalid result object")
    return {
        "ok": True,
        "command": "aivw run-recipe",
        "reference": reference,
        "through": through,
        "profile": profile,
        "exit_code": process.returncode,
        "aivw": payload,
        "delegation": {
            "transport": "controller-owned-authenticated-unix-socket",
            "credential_exposed_to_agent": False,
            "retry_performed": False,
        },
    }


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise AivwArgumentError(f"{label} must be a recipe-style identifier")
    return value


def _terminate_process_group(process: subprocess.Popen[str]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=5.0)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    process.wait()


def _last_line(value: str) -> str:
    lines = value.strip().splitlines()
    return lines[-1][:1000] if lines else ""


def _safe_process_error(value: str, token: str) -> str:
    text = value.replace(token, "<redacted>") if token else value
    return _last_line(text)


__all__ = [
    "AivwArgumentError",
    "AivwExecutionError",
    "run_aivw_recipe",
]
