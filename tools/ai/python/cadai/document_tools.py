"""Bounded, workspace-scoped project document discovery, reading and search."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import stat
import time
from pathlib import Path
from typing import Any, Optional, Union

MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_READ_CHARS = 16_000
MAX_LIST_RESULTS = 200
MAX_SEARCH_RESULTS = 100
MAX_SNIPPET_CHARS = 4_096
MAX_OUTPUT_BYTES = 65_536
MAX_SCAN_FILES = 2_000
MAX_SCAN_DEPTH = 12

class DocumentError(ValueError):
    """A user-correctable document request failure."""


def _text(value: Any, name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise DocumentError(f"{name} requires 1..{maximum} characters")
    if any(char in value for char in ("\x00", "\n", "\r")):
        raise DocumentError(f"{name} must be single-line text without NUL")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise DocumentError(f"{name} must be valid UTF-8") from exc
    return value


def _relative_path(value: Any, name: str = "path") -> str:
    return _text(value, name, 4096)


def _workspace(workspace: Union[str, Path]) -> Path:
    if workspace is None:
        raise DocumentError("document tools require an explicit workspace")
    try:
        root = Path(workspace).expanduser().resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise DocumentError("document workspace is unavailable") from exc
    if not root.is_dir():
        raise DocumentError("document workspace is not a directory")
    return root


def _resolve(value: str, workspace: Path, *, directory: Optional[bool] = None) -> Path:
    requested = Path(value).expanduser()
    candidate = requested if requested.is_absolute() else workspace / requested
    try:
        target = candidate.resolve(strict=True)
        target.relative_to(workspace)
    except (OSError, RuntimeError, ValueError) as exc:
        raise DocumentError(
            "document path must resolve inside the captured project workspace"
        ) from exc
    try:
        info = target.stat()
    except OSError as exc:
        raise DocumentError("document path is unavailable") from exc
    if directory is True and not stat.S_ISDIR(info.st_mode):
        raise DocumentError("document path must be a directory")
    if directory is False and not stat.S_ISREG(info.st_mode):
        raise DocumentError("document path must be a regular file")
    if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
        raise DocumentError("document path must be a regular file or directory")
    return target


def _decode(path: Path, data: bytes) -> tuple[str, str]:
    if b"\x00" in data:
        raise DocumentError("binary documents are not supported")
    # A non-UTF text file is still accepted when it contains ordinary text
    # bytes.  Reject control-heavy payloads before the latin-1 fallback so an
    # arbitrary binary blob cannot be presented as a document.
    if any(byte < 32 and byte not in (9, 10, 12, 13) or byte == 127 for byte in data):
        raise DocumentError("binary documents are not supported")
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig"), "utf-8-sig"
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        for encoding in ("gb18030", "latin-1"):
            try:
                return data.decode(encoding), encoding
            except UnicodeDecodeError:
                continue
    raise DocumentError(f"document is not a supported text encoding: {path.name}")


def _read(path: Path) -> tuple[str, str, int, str]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = -1
    try:
        fd = os.open(path, flags)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise DocumentError("document path must be a regular file")
        size = info.st_size
        if size > MAX_FILE_BYTES:
            raise DocumentError(f"document exceeds {MAX_FILE_BYTES} bytes")
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            data = handle.read(MAX_FILE_BYTES + 1)
    except DocumentError:
        raise
    except OSError as exc:
        raise DocumentError("document cannot be read") from exc
    finally:
        if fd >= 0:
            os.close(fd)
    if len(data) > MAX_FILE_BYTES:
        raise DocumentError(f"document exceeds {MAX_FILE_BYTES} bytes")
    text, encoding = _decode(path, data)
    return text, encoding, len(data), hashlib.sha256(data).hexdigest()


def _validate_common(arguments: dict[str, Any], allowed: set[str]) -> None:
    if not isinstance(arguments, dict) or set(arguments) - allowed:
        raise DocumentError("unknown document argument")


def _globs(value: Any) -> list[str]:
    if value is None:
        return ["*.md", "*.markdown", "*.txt", "*.rst", "*.json", "*.yaml", "*.yml", "*.xml", "*.csv"]
    if not isinstance(value, list) or len(value) > 20:
        raise DocumentError("globs must be a list of at most 20 strings")
    result = []
    for item in value:
        result.append(_text(item, "glob", 256))
    return result


def _matches(path: Path, globs: list[str]) -> bool:
    name = path.name
    relative = path.as_posix()
    return not globs or any(fnmatch.fnmatch(name, glob) or fnmatch.fnmatch(relative, glob) for glob in globs)


def _files(root: Path, globs: list[str], limit: int) -> tuple[list[Path], bool]:
    found: list[Path] = []
    truncated = False
    scanned = 0
    for current, dirs, names in os.walk(root, followlinks=False):
        try:
            depth = len(Path(current).relative_to(root).parts)
        except ValueError:
            continue
        if depth >= MAX_SCAN_DEPTH:
            dirs[:] = []
            truncated = True
        if scanned >= MAX_SCAN_FILES or len(found) >= limit:
            return found, True
        dirs[:] = sorted(name for name in dirs if not (Path(current) / name).is_symlink())
        for name in sorted(names):
            scanned += 1
            if scanned > MAX_SCAN_FILES:
                return found, True
            path = Path(current) / name
            try:
                if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
                    continue
                if not _matches(path, globs):
                    continue
            except OSError:
                continue
            if len(found) >= limit:
                truncated = True
                return found, truncated
            found.append(path)
    return found, truncated


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _bound_results(response: dict[str, Any], key: str) -> dict[str, Any]:
    """Keep structured document responses below the MCP response budget."""
    rows = response.get(key, [])
    original = len(rows)
    while rows and len(json.dumps(response, ensure_ascii=True).encode("utf-8")) > MAX_OUTPUT_BYTES:
        rows.pop()
    response["returned"] = len(rows)
    if len(rows) < original:
        response["complete"] = False
        response["truncated"] = True
        response["truncation_reason"] = "output_limit"
    return response


def validate_list(arguments: dict[str, Any]) -> None:
    _validate_common(arguments, {"path", "globs", "max_results"})
    _text(arguments.get("path", "."), "path", 4096)
    _globs(arguments.get("globs"))
    value = arguments.get("max_results", 100)
    if type(value) is not int or not 1 <= value <= MAX_LIST_RESULTS:
        raise DocumentError(f"max_results must be an integer in 1..{MAX_LIST_RESULTS}")


def list_documents(arguments: dict[str, Any], workspace: Union[str, Path]) -> dict[str, Any]:
    validate_list(arguments)
    root = _workspace(workspace)
    target = _resolve(arguments.get("path", "."), root, directory=True)
    paths, truncated = _files(target, _globs(arguments.get("globs")), arguments.get("max_results", 100))
    results = []
    for path in paths:
        try:
            results.append({"path": _relative(path, root), "bytes": path.stat().st_size})
        except OSError:
            continue
    return _bound_results({
        "ok": True,
        "root": str(root),
        "path": _relative(target, root) or ".",
        "results": results,
        "returned": len(results),
        "complete": not truncated,
        "truncated": truncated,
        "limits": {"max_results": MAX_LIST_RESULTS, "max_file_bytes": MAX_FILE_BYTES},
    }, "results")


def validate_read(arguments: dict[str, Any]) -> None:
    _validate_common(arguments, {"path", "offset", "limit"})
    _relative_path(arguments.get("path"))
    offset = arguments.get("offset", 0)
    limit = arguments.get("limit", 12_000)
    if type(offset) is not int or offset < 0:
        raise DocumentError("offset must be a non-negative integer")
    if type(limit) is not int or not 1 <= limit <= MAX_READ_CHARS:
        raise DocumentError(f"limit must be an integer in 1..{MAX_READ_CHARS}")


def read_document(arguments: dict[str, Any], workspace: Union[str, Path]) -> dict[str, Any]:
    validate_read(arguments)
    root = _workspace(workspace)
    target = _resolve(arguments["path"], root, directory=False)
    text, encoding, size, sha256 = _read(target)
    offset, limit = arguments.get("offset", 0), arguments.get("limit", 12_000)
    end = min(len(text), offset + limit)
    return {
        "ok": True,
        "path": _relative(target, root),
        "bytes": size,
        "sha256": sha256,
        "encoding": encoding,
        "offset": offset,
        "next_offset": end,
        "total_chars": len(text),
        "complete": end >= len(text),
        "text": text[offset:end],
    }


def validate_search(arguments: dict[str, Any]) -> None:
    _validate_common(arguments, {"pattern", "path", "globs", "case_sensitive", "max_results"})
    _text(arguments.get("pattern"), "pattern", 4096)
    _text(arguments.get("path", "."), "path", 4096)
    _globs(arguments.get("globs"))
    if type(arguments.get("case_sensitive", True)) is not bool:
        raise DocumentError("case_sensitive must be boolean")
    value = arguments.get("max_results", 50)
    if type(value) is not int or not 1 <= value <= MAX_SEARCH_RESULTS:
        raise DocumentError(f"max_results must be an integer in 1..{MAX_SEARCH_RESULTS}")


def search_documents(arguments: dict[str, Any], workspace: Union[str, Path]) -> dict[str, Any]:
    validate_search(arguments)
    root = _workspace(workspace)
    target = _resolve(arguments.get("path", "."), root, directory=True)
    pattern = arguments["pattern"]
    if not arguments.get("case_sensitive", True):
        pattern = pattern.casefold()
    paths, file_truncated = _files(target, _globs(arguments.get("globs")), MAX_LIST_RESULTS)
    results: list[dict[str, Any]] = []
    truncated = file_truncated
    for path in paths:
        try:
            text, _encoding, _size, _sha = _read(path)
        except DocumentError:
            continue
        for line_number, line in enumerate(text.splitlines(), 1):
            haystack = line if arguments.get("case_sensitive", True) else line.casefold()
            if pattern not in haystack:
                continue
            if len(results) >= arguments.get("max_results", 50):
                truncated = True
                break
            results.append({
                "path": _relative(path, root),
                "line_number": line_number,
                "text": line[:MAX_SNIPPET_CHARS],
                "text_truncated": len(line) > MAX_SNIPPET_CHARS,
            })
        if truncated and len(results) >= arguments.get("max_results", 50):
            break
    return _bound_results({
        "ok": True,
        "root": str(root),
        "path": _relative(target, root) or ".",
        "pattern": arguments["pattern"],
        "results": results,
        "returned": len(results),
        "complete": not truncated,
        "truncated": truncated,
        "limits": {
            "max_results": MAX_SEARCH_RESULTS,
            "max_file_bytes": MAX_FILE_BYTES,
            "max_scan_files": MAX_SCAN_FILES,
            "max_scan_depth": MAX_SCAN_DEPTH,
            "max_output_bytes": MAX_OUTPUT_BYTES,
        },
    }, "results")


DOCUMENT_TOOLS = [
    {
        "name": "list_project_documents",
        "title": "List project documents",
        "description": "List bounded text documents under the captured project workspace. Absolute paths are accepted only when they resolve inside that workspace; symlink escapes and special files are rejected.",
        "inputSchema": {"type": "object", "properties": {
            "path": {"type": "string", "default": ".", "maxLength": 4096},
            "globs": {"type": "array", "maxItems": 20, "items": {"type": "string", "maxLength": 256}},
            "max_results": {"type": "integer", "minimum": 1, "maximum": MAX_LIST_RESULTS, "default": 100},
        }, "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "read_project_document",
        "title": "Read project document",
        "description": "Read a bounded page of a UTF-8 or common text-encoded document under the captured project workspace. Returns SHA-256 and offsets so a large document can be read safely in pages.",
        "inputSchema": {"type": "object", "properties": {
            "path": {"type": "string", "minLength": 1, "maxLength": 4096},
            "offset": {"type": "integer", "minimum": 0, "default": 0},
            "limit": {"type": "integer", "minimum": 1, "maximum": MAX_READ_CHARS, "default": 12000},
        }, "required": ["path"], "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "search_project_documents",
        "title": "Search project documents",
        "description": "Search bounded text documents under the captured project workspace and return file paths, line numbers and clipped excerpts. Results are capped and never follow symlinked directories.",
        "inputSchema": {"type": "object", "properties": {
            "pattern": {"type": "string", "minLength": 1, "maxLength": 4096},
            "path": {"type": "string", "default": ".", "maxLength": 4096},
            "globs": {"type": "array", "maxItems": 20, "items": {"type": "string", "maxLength": 256}},
            "case_sensitive": {"type": "boolean", "default": True},
            "max_results": {"type": "integer", "minimum": 1, "maximum": MAX_SEARCH_RESULTS, "default": 50},
        }, "required": ["pattern"], "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
]
DOCUMENT_NAMES = frozenset(tool["name"] for tool in DOCUMENT_TOOLS)
_VALIDATORS = {"list_project_documents": validate_list, "read_project_document": validate_read, "search_project_documents": validate_search}
_HANDLERS = {"list_project_documents": list_documents, "read_project_document": read_document, "search_project_documents": search_documents}


def call_document_tool(name: str, arguments: dict[str, Any], workspace: Union[str, Path]) -> dict[str, Any]:
    if name not in DOCUMENT_NAMES:
        raise DocumentError("unknown document tool")
    _VALIDATORS[name](arguments)
    return _HANDLERS[name](arguments, workspace)
