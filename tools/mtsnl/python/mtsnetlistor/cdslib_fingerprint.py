"""Fingerprint recursive cds.lib includes without querying OA."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from typing import Mapping


_CDSLIB_ENVIRONMENT_VARIABLE = re.compile(
    r"\$(?:\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)\}|"
    r"(?P<plain>[A-Za-z_][A-Za-z0-9_]*))"
)


def _expand_include_for_fingerprint(
    value: str,
    base_directory: Path,
    environment: Mapping[str, str],
) -> Path | None:
    """Expand the subset of cds.lib paths understood by the common parser."""

    # ``$()`` install-root expressions are resolved by Cadence's
    # ``cdsLibDebug`` and deliberately unsupported by the pure parser.  Leave
    # them as an unresolved record so every such file gets a distinct key;
    # the authoritative provider still decides whether the expression works.
    if "$(" in value:
        return None
    missing = False

    def replace(match: re.Match[str]) -> str:
        nonlocal missing
        name = match.group("braced") or match.group("plain")
        if name not in environment:
            missing = True
            return match.group(0)
        return str(environment[name])

    expanded = _CDSLIB_ENVIRONMENT_VARIABLE.sub(replace, value)
    if missing:
        return None
    if expanded == "~" or expanded.startswith("~/"):
        home = str(environment.get("HOME", "")).strip()
        if not home:
            return None
        expanded = home + expanded[1:]
    elif expanded.startswith("~"):
        expanded = os.path.expanduser(expanded)
        if expanded.startswith("~"):
            return None
    path = Path(expanded)
    if not path.is_absolute():
        path = base_directory / path
    return path.expanduser().resolve()


def _source_cdslib_fingerprint_details(
    cds_library_file: Path,
    environment: Mapping[str, str],
) -> tuple[str, bool]:
    """Fingerprint a cds.lib and its recursive INCLUDE graph.

    The routine intentionally fingerprints definition files only.  OA cell
    contents are mutable and potentially enormous; a bounded cache TTL plus
    the explicit ``refresh``/``clear_source_catalog_cache`` controls avoids a
    recursive walk of every library on each project selection.
    """

    records: list[tuple[object, ...]] = []
    visited: set[Path] = set()
    complete = True

    def visit(path: Path, stack: tuple[Path, ...]) -> None:
        nonlocal complete
        path = path.expanduser().resolve()
        if path in stack:
            records.append(("cycle", str(path), " -> ".join(map(str, stack))))
            return
        if path in visited:
            records.append(("reference", str(path)))
            return
        visited.add(path)
        try:
            raw = path.read_bytes()
            stat = path.stat()
        except OSError as exc:
            complete = False
            records.append(("missing", str(path), type(exc).__name__))
            return
        records.append(
            (
                "file",
                str(path),
                int(stat.st_size),
                int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1e9))),
                hashlib.sha256(raw).hexdigest(),
            )
        )
        text = raw.decode("utf-8", errors="replace")
        for line_number, raw_line in enumerate(text.splitlines(), start=1):
            fields = raw_line.split()
            for index, field in enumerate(fields):
                if field.startswith("#") or field.startswith("--"):
                    fields = fields[:index]
                    break
            if len(fields) < 2:
                continue
            statement = fields[0].upper()
            if statement not in {"INCLUDE", "SOFTINCLUDE"}:
                continue
            include_value = fields[1]
            included = _expand_include_for_fingerprint(
                include_value, path.parent, environment
            )
            if included is None:
                complete = False
                records.append(
                    ("unresolved", str(path), line_number, statement, include_value)
                )
                continue
            if statement == "SOFTINCLUDE" and not included.is_file():
                # A missing SOFTINCLUDE is a stable part of the input as long
                # as the path remains absent.  Record its path so creation of
                # that file changes the fingerprint on the next request.
                records.append(("soft-missing", str(included)))
                continue
            visit(included, (*stack, path))

    visit(cds_library_file, ())
    encoded = json.dumps(records, ensure_ascii=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest(), complete
