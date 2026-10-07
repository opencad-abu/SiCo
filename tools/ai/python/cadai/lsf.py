"""Optional interactive LSF handoff for a managed AI Assistant session."""

from __future__ import annotations

import os
import shlex
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import NoReturn
from cadenv import preserve_virtuoso_environment

LSF_EXECUTABLE_ENV = "PROJ_AI_LSF_EXE"
LSF_ARGUMENTS_ENV = "PROJ_AI_LSF_ARGS"
LSF_HANDOFF_ENV = "SICO_AI_LSF_HANDOFF"
LSF_SUBMIT_DISPLAY_ENV = "SICO_AI_LSF_SUBMIT_DISPLAY"
LSF_XFORWARD_ENV = "SICO_AI_LSF_XFORWARD"
LSF_METADATA_ALIASES = {
    LSF_HANDOFF_ENV: "CAD_AI_LSF_HANDOFF",
    LSF_SUBMIT_DISPLAY_ENV: "CAD_AI_LSF_SUBMIT_DISPLAY",
    LSF_XFORWARD_ENV: "CAD_AI_LSF_XFORWARD",
}
_BSUB_CONTROL_REDIRECT_OPTIONS = {"-i", "-is", "-o", "-oo"}
_DEFAULT_SSH_XFORWARD_COMMAND = (
    "ssh -X -n -o BatchMode=yes -o StrictHostKeyChecking=yes"
)


def capture_virtuoso_environment(environment: dict[str, str]) -> None:
    """Retain submit-host module metadata before LSF/site prologs change it."""
    preserve_virtuoso_environment(environment)


def _resolve_lsf_executable(value: str, environment: Mapping[str, str]) -> str:
    if os.path.sep in value:
        candidate = Path(value).expanduser()
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate.absolute())
    else:
        resolved = shutil.which(value, path=environment.get("PATH"))
        if resolved:
            return str(Path(resolved).absolute())
    raise FileNotFoundError(f"cannot execute {LSF_EXECUTABLE_ENV}: {value}")


def _is_standard_bsub(configured: str, executable: str) -> bool:
    executable_names = {Path(configured).name, Path(executable).name}
    try:
        executable_names.add(Path(executable).resolve(strict=True).name)
    except OSError:
        pass
    return "bsub" in executable_names


def build_lsf_submission_command(
    session_command: Sequence[str],
    environment: Mapping[str, str] | None = None,
) -> list[str]:
    source = os.environ if environment is None else environment
    configured = source.get(LSF_EXECUTABLE_ENV, "").strip()
    if not configured:
        raise ValueError(f"{LSF_EXECUTABLE_ENV} is not configured")
    if "\0" in configured:
        raise ValueError(f"{LSF_EXECUTABLE_ENV} contains a NUL byte")
    executable = _resolve_lsf_executable(configured, source)

    raw_arguments = source.get(LSF_ARGUMENTS_ENV, "")
    try:
        arguments = shlex.split(raw_arguments, posix=True)
    except ValueError as exc:
        raise ValueError(f"invalid {LSF_ARGUMENTS_ENV}: {exc}") from exc
    if any("\0" in argument for argument in arguments):
        raise ValueError(f"{LSF_ARGUMENTS_ENV} contains a NUL byte")
    if _is_standard_bsub(configured, executable):
        if any(argument in {"-Ip", "-Is"} for argument in arguments):
            raise ValueError(f"{LSF_ARGUMENTS_ENV} must use plain bsub -I mode")
        if not arguments or arguments[0] != "-I":
            raise ValueError(f"{LSF_ARGUMENTS_ENV} must start with plain bsub -I mode")
        if any(
            argument.split("=", 1)[0] in _BSUB_CONTROL_REDIRECT_OPTIONS
            for argument in arguments
        ):
            raise ValueError(
                f"{LSF_ARGUMENTS_ENV} must not redirect bsub stdin or stdout"
            )

    remote_command = list(session_command)
    if not remote_command or not remote_command[0]:
        raise ValueError("remote AI session command is empty")
    if any(not isinstance(argument, str) or "\0" in argument for argument in remote_command):
        raise ValueError("remote AI session command contains an invalid argument")
    return [executable, *arguments, shlex.join(remote_command)]


def exec_lsf_session(
    session_command: Sequence[str],
    environment: Mapping[str, str] | None = None,
) -> NoReturn:
    source = dict(os.environ if environment is None else environment)
    capture_virtuoso_environment(source)
    command = build_lsf_submission_command(session_command, source)
    configured = source[LSF_EXECUTABLE_ENV].strip()
    standard_bsub = _is_standard_bsub(configured, command[0])
    xforward_requested = "-XF" in command[1:-1]
    for old in LSF_METADATA_ALIASES.values():
        source.pop(old, None)
    source[LSF_HANDOFF_ENV] = "1"
    source[LSF_SUBMIT_DISPLAY_ENV] = source.get("DISPLAY", "").strip()
    source[LSF_XFORWARD_ENV] = (
        "1" if standard_bsub and xforward_requested else "0" if standard_bsub else "site"
    )
    if standard_bsub:
        # bsub status lines must not contaminate the controller's JSONL stream.
        if not source.get("BSUB_QUIET", "").strip():
            source["BSUB_QUIET"] = "1"
        if xforward_requested:
            source.setdefault("LSB_SSH_XFORWARD_CMD", _DEFAULT_SSH_XFORWARD_COMMAND)
    source.pop(LSF_EXECUTABLE_ENV, None)
    source.pop(LSF_ARGUMENTS_ENV, None)
    os.execvpe(command[0], command, source)


__all__ = [
    "LSF_ARGUMENTS_ENV",
    "LSF_EXECUTABLE_ENV",
    "LSF_HANDOFF_ENV",
    "LSF_SUBMIT_DISPLAY_ENV",
    "LSF_XFORWARD_ENV",
    "build_lsf_submission_command",
    "exec_lsf_session",
]
