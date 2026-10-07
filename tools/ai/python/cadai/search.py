"""Bounded ripgrep results for the local Search MCP tool."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

from .search_process import run_search_process
from .search_runtime import resolve_rg
from .search_tools import search_options, search_path
from .socket_server import RequestFailure

MAX_RESULT_BYTES = 65536
MAX_RECORD_BYTES = 256 * 1024
MAX_LINE_BYTES = 4096


def _size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=True).encode("utf-8"))


def _decode(field: dict[str, str]) -> tuple[str, str | None]:
    if "text" in field:
        return field["text"], None
    encoded = field["bytes"]
    return base64.b64decode(encoded).decode("utf-8", errors="replace"), encoded


class _Results:
    def __init__(self, mode: str, limit: int) -> None:
        self.mode = mode
        self.limit = limit
        self.buffer = bytearray()
        self.items: list[dict[str, Any]] = []
        self.size = 0
        self.line_truncated = False

    def consume(self, chunk: bytes) -> str | None:
        self.buffer.extend(chunk)
        delimiter = b"\x00" if self.mode == "files" else b"\n"
        while delimiter in self.buffer:
            offset = self.buffer.index(delimiter)
            if offset > MAX_RECORD_BYTES:
                return "record_limit"
            record = bytes(self.buffer[:offset])
            del self.buffer[: offset + 1]
            if self.mode == "files":
                item = {"path": record.decode("utf-8", errors="replace")}
                try:
                    record.decode("utf-8")
                except UnicodeDecodeError:
                    item["path_bytes_base64"] = base64.b64encode(record).decode("ascii")
            else:
                event = json.loads(record)
                if event["type"] != "match":
                    continue
                data = event["data"]
                path, encoded = _decode(data["path"])
                line, _ = _decode(data["lines"])
                raw_line = line.rstrip("\r\n").encode("utf-8")
                clipped = len(raw_line) > MAX_LINE_BYTES
                self.line_truncated |= clipped
                item = {
                    "path": path,
                    "line_number": data["line_number"],
                    "text": raw_line[:MAX_LINE_BYTES].decode("utf-8", errors="ignore"),
                    "text_truncated": clipped,
                    "byte_column": data["submatches"][0]["start"] + 1,
                }
                if "bytes" in data["lines"]:
                    item["text_encoding_replaced"] = True
                if encoded is not None:
                    item["path_bytes_base64"] = encoded
            if len(self.items) >= self.limit:
                return "max_results"
            size = _size(item) + 2
            if self.size + size > MAX_RESULT_BYTES - 8192:
                return "output_limit"
            self.items.append(item)
            self.size += size
        return "record_limit" if len(self.buffer) > MAX_RECORD_BYTES else None


def search(arguments: dict[str, Any], workspace: Path | None) -> dict[str, Any]:
    options = search_options(arguments)
    target, cwd = search_path(options["path"], workspace, options["mode"])
    try:
        executable = resolve_rg()
    except OSError as exc:
        raise RequestFailure("search_unavailable", str(exc)) from exc
    command = [
        str(executable),
        "--no-config",
        "--color=never",
        "--no-follow",
        "--no-mmap",
        "--threads=1",
        "--no-ignore-global",
    ]
    if options["include_hidden"]:
        command.append("--hidden")
    if not options["respect_ignore"]:
        command.append("--no-ignore")
    command.extend("--glob=" + glob for glob in options["globs"])
    command.append("--glob=!.git/**")
    if options["mode"] == "files":
        command.extend(["--files", "--null"])
    else:
        command.extend(["--json", "--line-number", "--max-filesize=8M", "--engine=default"])
        command.append("--case-sensitive" if options["case_sensitive"] else "--ignore-case")
        if options["fixed_strings"]:
            command.append("--fixed-strings")
        command.extend(["--regexp", options["pattern"]])
    command.extend(["--", "." if target == cwd else "./" + target.name])
    results = _Results(options["mode"], options["max_results"])
    try:
        status, stderr, reason = run_search_process(
            command,
            cwd,
            options["timeout_seconds"],
            results.consume,
        )
    except (OSError, ValueError, KeyError, IndexError) as exc:
        raise RequestFailure("search_failed", str(exc)) from exc
    ok = reason != "timeout" and (status in (0, 1) or (reason is not None and status == -9))
    response = {
        "ok": ok,
        "mode": options["mode"],
        "root": os.fsencode(cwd).decode("utf-8", errors="replace"),
        "results": results.items,
        "returned": len(results.items),
        "complete": reason is None and ok and not results.line_truncated,
        "truncated": reason is not None or results.line_truncated,
        "truncation_reason": reason or ("line_limit" if results.line_truncated else None),
        "timed_out": reason == "timeout",
        "exit_code": status,
        "limits": {
            "max_results": options["max_results"],
            "max_output_bytes": MAX_RESULT_BYTES,
            "max_file_bytes": 8 * 1024 * 1024,
            "timeout_seconds": options["timeout_seconds"],
        },
    }
    if stderr:
        response["diagnostic"] = stderr
    if not ok:
        response["code"] = "search_timeout" if reason == "timeout" else "search_failed"
    while _size(response) > MAX_RESULT_BYTES and results.items:
        results.items.pop()
        response.update(
            returned=len(results.items),
            complete=False,
            truncated=True,
            truncation_reason="output_limit",
        )
    return response
