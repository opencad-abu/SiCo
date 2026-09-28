"""JSON-safe serializers for catalog and workflow artifacts."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any


def json_value(value: Any) -> Any:
    """Convert core dataclasses and paths to deterministic JSON values."""

    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return {key: json_value(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    return value
