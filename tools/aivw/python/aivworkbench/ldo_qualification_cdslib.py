"""Resolve the approved cds.lib include and mapping graph."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from .ldo_qualification_paths import (
    qualification_external_file_record,
    qualification_has_symlink_below,
)
from .workspace import sha256_file

_DEFINE = re.compile(r"^\s*DEFINE\s+(?P<name>[A-Za-z_][A-Za-z0-9_$.-]*)\s+(?P<value>\S+)\s*$")


_CDS_INCLUDE = re.compile(
    r"^\s*(?P<kind>SOFTINCLUDE|INCLUDE)\s+(?P<path>\"[^\"]+\"|'[^']+'|\S+)\s*$",
    re.IGNORECASE,
)


def _expand_cds_value(value: str, environment: Mapping[str, str]) -> str | None:
    """Expand only explicit environment variables; Cadence compute syntax is not guessed."""

    expanded = value
    for _ in range(4):
        before = expanded
        expanded = re.sub(
            r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)",
            lambda match: str(environment.get(match.group(1) or match.group(2), match.group(0))),
            expanded,
        )
        if expanded == before:
            break
    if "$" in expanded or "$(" in expanded:
        return None
    return expanded


def qualification_allowed_cds_path(path: Path, allowed_roots: Sequence[Path]) -> bool:
    resolved = path.resolve(strict=False)
    return any(resolved.is_relative_to(root.resolve(strict=False)) for root in allowed_roots)


def qualification_parse_cds_lib(
    path: Path,
    root: Path,
    required: Sequence[str],
    *,
    allowed_roots: Sequence[Path] | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Resolve a bounded cds.lib include graph without invoking Cadence."""

    definitions: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    warnings: list[str] = []
    files: list[dict[str, Any]] = []
    env = {str(key): str(value) for key, value in (environment or {}).items()}
    roots = tuple(item.resolve(strict=False) for item in (allowed_roots or (root,)))
    visited: set[Path] = set()
    active: set[Path] = set()

    def visit(current: Path, *, required_file: bool) -> None:
        resolved = current.resolve(strict=False)
        if resolved in active:
            errors.append("cds.lib include cycle detected: %s" % resolved)
            return
        if not qualification_allowed_cds_path(resolved, roots) or resolved.is_symlink() or not resolved.is_file():
            message = "cds.lib include is unavailable or outside approved mapping roots: %s" % current
            (errors if required_file else warnings).append(message)
            return
        if resolved in visited:
            return
        active.add(resolved)
        visited.add(resolved)
        try:
            files.append(qualification_external_file_record(resolved))
            try:
                text = resolved.read_text(encoding="utf-8", errors="strict")
            except (OSError, UnicodeError) as exc:
                errors.append("cannot read cds.lib include %s: %s" % (resolved, exc))
                return
            for line_number, raw in enumerate(text.splitlines(), 1):
                line = raw.strip()
                if not line or line.startswith("#") or line.startswith("--"):
                    continue
                include = _CDS_INCLUDE.fullmatch(line)
                if include:
                    include_kind = include.group("kind").upper()
                    include_text = include.group("path")
                    if include_text[:1] in {"'", '"'} and include_text[-1:] == include_text[:1]:
                        include_text = include_text[1:-1]
                    expanded = _expand_cds_value(include_text, env)
                    if expanded is None:
                        message = "cds.lib %s has unresolved include variable at %s:%d" % (include_kind, resolved, line_number)
                        (errors if include_kind == "INCLUDE" else warnings).append(message)
                    else:
                        child = Path(expanded)
                        if not child.is_absolute():
                            child = resolved.parent / child
                        visit(child, required_file=include_kind == "INCLUDE")
                    continue
                match = _DEFINE.fullmatch(line)
                if not match:
                    continue
                name = match.group("name")
                value = match.group("value")
                record: dict[str, Any] = {
                    "value": value,
                    "line": line_number,
                    "source": str(resolved),
                    "resolved": False,
                }
                expanded = _expand_cds_value(value, env)
                if expanded is None:
                    record["resolution"] = "tool_variable_required"
                else:
                    candidate_raw = Path(expanded)
                    if not candidate_raw.is_absolute():
                        candidate_raw = resolved.parent / candidate_raw
                    candidate = candidate_raw.resolve(strict=False)
                    if (
                        qualification_allowed_cds_path(candidate, roots)
                        and not qualification_has_symlink_below(next((item for item in roots if candidate.is_relative_to(item)), roots[0]), candidate)
                        and candidate.is_dir()
                    ):
                        record["path"] = str(candidate)
                        record["resolved"] = True
                    else:
                        record["resolution"] = "outside_or_missing"
                definitions[name] = record
        finally:
            active.remove(resolved)

    visit(path, required_file=True)
    missing = [name for name in required if name not in definitions or not definitions[name].get("resolved")]
    if missing:
        errors.append("cds.lib required mappings are unresolved: %s" % ", ".join(missing))
    return {
        "path": str(path),
        "sha256": sha256_file(path) if path.is_file() else None,
        "definitions": definitions,
        "required": list(required),
        "missing": missing,
        "errors": sorted(set(errors)),
        "warnings": sorted(set(warnings)),
        "files": files,
    }
