"""Index declared Spectre model files and required corner sections."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Sequence

from .ldo_qualification_paths import qualification_file_record, qualification_safe_child

_INCLUDE = re.compile(r"^\s*include\s+[\"'](?P<path>[^\"']+)[\"'](?:\s+section\s*=\s*(?P<section>[A-Za-z0-9_.+-]+))?", re.IGNORECASE)


_SECTION = re.compile(r"^\s*section\s+(?P<name>[A-Za-z0-9_.+-]+)", re.IGNORECASE | re.MULTILINE)


def qualification_model_identity(model_root: Path, root: Path, required_sections: Sequence[str]) -> dict[str, Any]:
    errors: list[str] = []
    records: list[dict[str, Any]] = []
    main = model_root / "gpdk045.scs"
    try:
        main = qualification_safe_child(root, str(main.relative_to(root)), kind="file")
    except ValueError as exc:
        return {
            "root": str(model_root),
            "main": None,
            "sections": [],
            "required_sections": list(required_sections),
            "missing_sections": list(required_sections),
            "files": [],
            "errors": [str(exc)],
        }
    records.append(qualification_file_record(main, root))
    try:
        text = main.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeError) as exc:
        errors.append(str(exc))
        text = ""
    sections = sorted(set(match.group("name") for match in _SECTION.finditer(text)))
    missing_sections = [item for item in required_sections if item not in sections]
    if missing_sections:
        errors.append("model corner sections are missing: %s" % ", ".join(missing_sections))
    for match in _INCLUDE.finditer(text):
        relative = match.group("path")
        try:
            included = qualification_safe_child(model_root, relative, kind="file")
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if included not in [Path(root / item["path"]).resolve() for item in records]:
            records.append(qualification_file_record(included, root))
    return {
        "root": str(model_root),
        "main": qualification_file_record(main, root),
        "sections": sections,
        "required_sections": list(required_sections),
        "missing_sections": missing_sections,
        "files": records,
        "errors": errors,
    }
