"""Local check_skill tool and source reader shared with the execution gate."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

from .runtime import MAX_EVAL_BYTES, MAX_SPOOL_BYTES
from .skill_check import check_source
from .socket_server import RequestFailure

SKILL_CHECK_TOOL = {
    "name": "check_skill",
    "title": "Check SKILL without execution",
    "description": (
        "Check SKILL delimiters, strings, comments and the function-definition and snippet "
        "policy (procedure, lambda, prog, let) without contacting Virtuoso. "
        "Supply exactly one of code or path. "
        "Rejects use of ']' to close parentheses. Returns bounded diagnostics, a source hash "
        "and executed=false. This is not a complete syntax or logic check."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "code": {"type": "string", "minLength": 1, "maxLength": MAX_EVAL_BYTES},
            "path": {"type": "string", "minLength": 1},
        },
        "oneOf": [{"required": ["code"]}, {"required": ["path"]}],
        "additionalProperties": False,
    },
    "annotations": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
}


def read_skill_file(requested_path: str, workspace: Path) -> tuple[Path, bytes]:
    """Read one bounded regular .il/.ils file once, preserving its exact bytes."""
    try:
        requested = Path(requested_path).expanduser()
        is_relative = not requested.is_absolute()
        path = workspace / requested if is_relative else requested
        path = path.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise RequestFailure("file_unavailable", str(exc)) from exc
    try:
        path.relative_to(workspace.resolve())
    except ValueError as exc:
        raise RequestFailure(
            "invalid_file", "SKILL path must resolve inside the workspace"
        ) from exc
    if path.suffix.lower() not in {".il", ".ils"}:
        raise RequestFailure("invalid_file", "SKILL source must end in .il or .ils")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = -1
    try:
        fd = os.open(path, flags)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SPOOL_BYTES:
            raise RequestFailure("invalid_file", "SKILL source must be a bounded regular file")
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            source = handle.read(MAX_SPOOL_BYTES + 1)
    except OSError as exc:
        raise RequestFailure("file_unavailable", str(exc)) from exc
    finally:
        if fd >= 0:
            os.close(fd)
    if len(source) > MAX_SPOOL_BYTES:
        raise RequestFailure("invalid_file", "SKILL source exceeds the size limit")
    return path, source


def check_skill(arguments: dict[str, Any], workspace: Path | None) -> dict[str, Any]:
    if set(arguments) not in ({"code"}, {"path"}):
        raise RequestFailure("invalid_params", "check_skill requires exactly one of code or path")
    key = next(iter(arguments))
    value = arguments[key]
    if not isinstance(value, str) or not value.strip():
        raise RequestFailure("invalid_params", f"{key} must be a nonempty string")
    if key == "path":
        path, source = read_skill_file(value, workspace if workspace is not None else Path.cwd())
        return check_source(source, source_name=str(path))
    try:
        source = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise RequestFailure("invalid_params", "code must be valid UTF-8 text") from exc
    if len(source) > MAX_EVAL_BYTES:
        raise RequestFailure("payload_too_large", f"code exceeds {MAX_EVAL_BYTES} UTF-8 bytes")
    return check_source(source)
