"""Validated artifact references returned to manifest consumers."""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path



@dataclass(frozen=True)
class ResolvedArtifact:
    source_manifest: Path
    artifact_manifest: Path
    artifact_root: Path
    path: Path
    relative_path: str
    sha256: str
    size: int
    producer_kind: str
    storage_role: str


@dataclass(frozen=True)
class ResolvedArtifactLocator:
    source_manifest: Path
    artifact_manifest: Path
    artifact_root: Path
    path: Path
    relative_path: str
    kind: str
    exists: bool
    producer: str
    sha256: str | None
    size: int | None
    storage_role: str

