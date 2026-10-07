"""Local Search MCP contract and argument validation (Python 3.9, no Qt)."""

from __future__ import annotations

import stat
from pathlib import Path
from typing import Any

from .socket_server import RequestFailure

SEARCH_TOOL = {
    "name": "Search",
    "title": "Search local text or file names",
    "description": (
        "Read-only local search using bundled ripgrep; no system rg required. "
        "mode=content searches text (literal by default), mode=files lists paths using globs. "
        "path defaults to the session workspace; relative paths stay inside it, absolute "
        "paths select an explicit external file/directory. Does not follow symlinks. "
        "Respects project ignore files and skips hidden files by default. Content files "
        "over 8 MiB are skipped; binary detection is ripgrep's default. Returns at most "
        "64 KiB of JSON, with truncation/timeout details; narrow the search if incomplete. "
        "Use search_skill_api for Cadence API documentation and inspection tools for OA data."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "mode": {"type": "string", "enum": ["content", "files"], "default": "content"},
            "pattern": {
                "type": "string",
                "minLength": 1,
                "maxLength": 4096,
                "description": "Required in content mode; single-line text or regex.",
            },
            "path": {"type": "string", "minLength": 1, "maxLength": 4096, "default": "."},
            "globs": {
                "type": "array",
                "maxItems": 20,
                "items": {"type": "string", "minLength": 1, "maxLength": 256},
                "description": "rg glob filters, e.g. ['*.il', '*.ils', '!vendor/**'].",
            },
            "fixed_strings": {"type": "boolean", "default": True},
            "case_sensitive": {"type": "boolean", "default": True},
            "include_hidden": {"type": "boolean", "default": False},
            "respect_ignore": {"type": "boolean", "default": True},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100},
            "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 30, "default": 10},
        },
        "additionalProperties": False,
    },
    "annotations": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
}


def _text(value: Any, name: str, limit: int) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise RequestFailure("invalid_params", f"{name} requires 1..{limit} characters")
    if any(c in value for c in ("\x00", "\n", "\r")):
        raise RequestFailure("invalid_params", f"{name} must be single-line text without NUL")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise RequestFailure("invalid_params", f"{name} must be valid UTF-8") from exc
    return value


def search_options(arguments: dict[str, Any]) -> dict[str, Any]:
    properties = SEARCH_TOOL["inputSchema"]["properties"]
    if set(arguments) - set(properties):
        raise RequestFailure("invalid_params", "unknown Search argument")
    options = {key: schema["default"] for key, schema in properties.items() if "default" in schema}
    options.update(arguments)
    if options["mode"] not in ("content", "files"):
        raise RequestFailure("invalid_params", "mode must be content or files")
    if options["mode"] == "content":
        _text(options.get("pattern"), "pattern", 4096)
    elif any(key in arguments for key in ("pattern", "fixed_strings", "case_sensitive")):
        raise RequestFailure("invalid_params", "files mode uses globs, not text matching options")
    _text(options["path"], "path", 4096)
    for key in ("fixed_strings", "case_sensitive", "include_hidden", "respect_ignore"):
        if type(options[key]) is not bool:
            raise RequestFailure("invalid_params", f"{key} must be boolean")
    for key, maximum in (("max_results", 1000), ("timeout_seconds", 30)):
        if type(options[key]) is not int or not 1 <= options[key] <= maximum:
            raise RequestFailure("invalid_params", f"{key} must be an integer in 1..{maximum}")
    globs = options.setdefault("globs", [])
    if not isinstance(globs, list) or len(globs) > 20:
        raise RequestFailure("invalid_params", "globs must be a list of at most 20 glob strings")
    for glob in globs:
        _text(glob, "glob", 256)
    return options


def search_path(value: str, workspace: Path | None, mode: str) -> tuple[Path, Path]:
    try:
        base = (workspace if workspace is not None else Path.cwd()).resolve(strict=True)
        requested = Path(value).expanduser()
        candidate = base / requested
        if candidate.is_symlink():
            raise RequestFailure("invalid_path", "Search does not accept a symlink target")
        target = candidate.resolve(strict=True)
        if not requested.is_absolute():
            try:
                target.relative_to(base)
            except ValueError as exc:
                raise RequestFailure(
                    "invalid_path", "relative Search path escapes the workspace"
                ) from exc
        info = target.stat()
        if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            raise RequestFailure("invalid_path", "Search requires a regular file or directory")
        if mode == "content" and stat.S_ISREG(info.st_mode) and info.st_size > 8 * 1024 * 1024:
            raise RequestFailure("invalid_path", "Search content file exceeds 8 MiB")
        return target, target if target.is_dir() else target.parent
    except (OSError, RuntimeError, ValueError) as exc:
        raise RequestFailure("search_unavailable", str(exc)) from exc
