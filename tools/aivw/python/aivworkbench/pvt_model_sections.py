"""Conservative approved model section declaration verification."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Mapping

from .artifact_paths import path_has_symlink_component

_SAFE_SECTION = re.compile(r"^[A-Za-z0-9_.+~-]+$")
_SECTION_DECLARATION = re.compile(r"^\s*section\s+([A-Za-z0-9_.+~-]+)(?:\s*(?:\{|$))", re.IGNORECASE)

def verify_approved_model_sections(
    model_path: Path,
    section_by_value: Mapping[str, str],
) -> dict[str, Any]:
    """Verify explicit section declarations in one approved model file.

    This is intentionally a conservative declaration check, not a Spectre
    parser.  It recognizes line-oriented ``section NAME`` declarations and
    reports missing/duplicate names.  A future adapter may add a stronger
    parser while retaining this result shape.
    """

    if not isinstance(section_by_value, Mapping):
        return {
            "status": "BLOCKED_INPUT",
            "code": "pvt_model_sections_invalid",
            "required": [],
            "found": [],
            "missing": [],
            "duplicates": [],
            "invalid": [],
        }
    if any(
        not isinstance(key, str)
        or not isinstance(value, str)
        or not _SAFE_SECTION.fullmatch(value)
        for key, value in section_by_value.items()
    ):
        return {
            "status": "BLOCKED_INPUT",
            "code": "pvt_model_sections_invalid",
            "required": [],
            "found": [],
            "missing": [],
            "duplicates": [],
            "invalid": sorted(
                str(value)
                for value in section_by_value.values()
                if not isinstance(value, str) or not _SAFE_SECTION.fullmatch(value)
            ),
        }
    required = list(section_by_value.values())
    if not required:
        return {
            "status": "BLOCKED_INPUT",
            "code": "pvt_model_sections_invalid",
            "required": [],
            "found": [],
            "missing": [],
            "duplicates": [],
        }
    if not isinstance(model_path, Path) or not model_path.is_absolute():
        return {
            "status": "BLOCKED_ENVIRONMENT",
            "code": "pvt_model_file_unavailable",
            "model_file": str(model_path),
        }
    try:
        unsafe = (
            model_path.is_symlink()
            or not model_path.is_file()
            or path_has_symlink_component(model_path.parent, model_path)
        )
    except (OSError, RuntimeError):
        unsafe = True
    if unsafe:
        return {
            "status": "BLOCKED_ENVIRONMENT",
            "code": "pvt_model_file_unavailable",
            "model_file": str(model_path),
        }
    try:
        text = model_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, RuntimeError) as exc:
        return {
            "status": "BLOCKED_ENVIRONMENT",
            "code": "pvt_model_file_unreadable",
            "model_file": str(model_path),
            "detail": str(exc),
        }
    found: list[str] = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(("//", "#", "*")):
            continue
        match = _SECTION_DECLARATION.match(line)
        if match:
            found.append(match.group(1))
    required_set = set(required)
    found_set = set(found)
    duplicates = sorted({name for name in found if found.count(name) > 1})
    missing = sorted(required_set - found_set)
    invalid = sorted(name for name in required_set if not _SAFE_SECTION.fullmatch(name))
    status = "PASS" if not missing and not duplicates and not invalid else "FAIL"
    return {
        "status": status,
        "code": "model_sections_verified" if status == "PASS" else "pvt_model_sections_missing",
        "model_file": str(model_path),
        "required": sorted(required_set),
        "found": sorted(found_set),
        "missing": missing,
        "duplicates": duplicates,
        "invalid": invalid,
    }


__all__ = ["verify_approved_model_sections"]
