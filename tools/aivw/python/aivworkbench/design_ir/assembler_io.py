"""Bounded file and record helpers for the DesignIR snapshot assembler."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..agent.context import ArtifactLocator

MAX_DESIGN_IR_RECORDS = 4096
MAX_DESIGN_IR_FIELD_BYTES = 256 * 1024


def payload_root(value: str | Path) -> Path:
    raw_root = Path(value).expanduser()
    if raw_root.is_symlink():
        raise ValueError("payload root must be a real directory")
    root = raw_root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("payload root must be a real directory")
    return root


def payload_artifact(root: Path, relative: str) -> Path:
    path = Path(relative)
    if (
        path.is_absolute()
        or ".." in path.parts
        or "\\" in relative
        or not relative.strip()
        or any(part in {"", "."} for part in relative.split("/"))
    ):
        raise ValueError("normalized_structure must be a payload-relative path")
    current = root
    for component in path.parts:
        current = current / component
        if current.is_symlink():
            raise ValueError("normalized_structure traverses a symlink")
    resolved = (root / path).resolve(strict=False)
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ValueError("normalized_structure is outside the payload")
    return resolved


def read_json(path: Path) -> dict[str, Any]:
    if path.stat().st_size > MAX_DESIGN_IR_FIELD_BYTES:
        raise ValueError("normalized structure exceeds the bounded artifact size")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"normalized structure is invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("normalized structure root must be an object")
    return value


def records(value: Any, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > MAX_DESIGN_IR_RECORDS:
        raise ValueError(f"normalized {field} exceeds the bounded record limit")
    result: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ValueError(f"normalized {field}[{index}] is not an object")
        result.append(dict(item))
    return result


def bounded_object(value: Mapping[str, Any], field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_DESIGN_IR_FIELD_BYTES:
        raise ValueError(f"{field} exceeds the bounded size")
    return json.loads(encoded)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def locator(path: Path, root: Path, media_type: str) -> ArtifactLocator:
    return ArtifactLocator(
        path.relative_to(root).as_posix(),
        sha256=sha256_file(path),
        size=path.stat().st_size,
        media_type=media_type,
    )


__all__ = [
    "MAX_DESIGN_IR_FIELD_BYTES",
    "MAX_DESIGN_IR_RECORDS",
    "bounded_object",
    "locator",
    "payload_artifact",
    "payload_root",
    "read_json",
    "records",
    "sha256_file",
]
