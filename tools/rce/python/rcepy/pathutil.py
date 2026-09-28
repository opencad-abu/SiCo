"""Strict expansion and resolution for configured filesystem paths."""

from __future__ import annotations

import os
from pathlib import Path
from cadconfig.paths import resolve_path
from typing import Any
from cadenv import preserve_eda_temp_environment, restore_eda_temp_environment
from sicostate import project_directory
from sicotemp import initialize_project, selected_state


def cad_temp_dir(*parts: str, create: bool = False) -> Path:
    """Compatibility facade for the shared launch root; preserve NAS spelling."""
    root = selected_state(create=create)
    if not parts:
        return root
    return project_directory(root.parent, "/".join(parts), create=create)


def cad_temp_environment(
    environment: dict[str, str] | None = None,
) -> dict[str, str]:
    """Return isolated flow scratch under the shared project root."""
    result = os.environ.copy() if environment is None else environment.copy()
    initialize_project(result)
    return result


def read_name_source(value: Any, base: Path) -> list[str]:
    """Read an existing name-list file, otherwise preserve the value as names."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return _unique_names(
            str(item).strip() for item in value if str(item).strip()
        )
    text = str(value).strip()
    try:
        path = resolve_path(text, base)
    except ValueError:
        path = None
    if text and path is not None and path.is_file():
        items: list[str] = []
        for line in path.read_text(errors="ignore").splitlines():
            head = line.split("#", 1)[0].strip()
            if head:
                items.extend(head.replace(",", " ").split())
        return _unique_names(items)
    if text.startswith("(") and text.endswith(")"):
        return _unique_names(
            item for item in text[1:-1].replace('"', "").split() if item
        )
    return _unique_names(
        item for item in text.replace(",", " ").split() if item
    )


def _unique_names(items: Any) -> list[str]:
    """Deduplicate names while retaining the user's ordering."""
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
