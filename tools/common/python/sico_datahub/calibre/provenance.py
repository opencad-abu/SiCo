"""Provenance bound to one Calibre runset and one checked candidate."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional

from ..codec import ArtifactRef
from ._validation import artifact, count, digest, mapping, text


@dataclass(frozen=True)
class CalibreProvenance:
    runset: ArtifactRef
    dependencies_sha256: str
    tool: str = "calibre"
    tool_version: str = ""
    run_mode: str = ""
    top_cell: str = ""
    layer_map: Optional[ArtifactRef] = None
    candidate: Optional[ArtifactRef] = None
    source_layout: Optional[ArtifactRef] = None
    effective_deck: Optional[ArtifactRef] = None
    effective_config: Optional[ArtifactRef] = None
    source_netlist: Optional[ArtifactRef] = None
    options: Mapping[str, object] = field(default_factory=dict)
    started_at: str = ""
    finished_at: str = ""
    run_ref: str = ""

    def __post_init__(self):
        artifact(self.runset, "runset")
        digest(self.dependencies_sha256)
        text(self.tool, "tool", limit=64)
        text(self.tool_version, "tool_version", empty=True, limit=128)
        text(self.run_mode, "run_mode", empty=True, limit=64)
        text(self.top_cell, "top_cell", empty=True, limit=512)
        artifact(self.layer_map, "layer_map", optional=True)
        artifact(self.candidate, "candidate", optional=True)
        artifact(self.source_layout, "source_layout", optional=True)
        artifact(self.effective_deck, "effective_deck", optional=True)
        artifact(self.effective_config, "effective_config", optional=True)
        artifact(self.source_netlist, "source_netlist", optional=True)
        mapping(dict(self.options), "Provenance options")
        text(self.started_at, "started_at", empty=True, limit=64)
        text(self.finished_at, "finished_at", empty=True, limit=64)
        text(self.run_ref, "run_ref", empty=True, limit=128)

    def to_dict(self):
        return {
            "runset": self.runset.to_dict(),
            "dependencies_sha256": self.dependencies_sha256,
            "tool": self.tool,
            "tool_version": self.tool_version,
            "run_mode": self.run_mode,
            "top_cell": self.top_cell,
            "layer_map": self.layer_map.to_dict() if self.layer_map else None,
            "candidate": self.candidate.to_dict() if self.candidate else None,
            "source_layout": self.source_layout.to_dict() if self.source_layout else None,
            "effective_deck": (
                self.effective_deck.to_dict() if self.effective_deck else None
            ),
            "effective_config": (
                self.effective_config.to_dict() if self.effective_config else None
            ),
            "source_netlist": (
                self.source_netlist.to_dict() if self.source_netlist else None
            ),
            "options": dict(self.options),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "run_ref": self.run_ref,
        }


@dataclass(frozen=True)
class RunsetIdentity:
    runset: ArtifactRef
    dependencies_sha256: str
    dependency_count: int = 1
    tool_version: str = ""

    def __post_init__(self):
        artifact(self.runset, "runset")
        digest(self.dependencies_sha256)
        count(self.dependency_count, "dependency count", allow_zero=False)
        text(self.tool_version, "tool_version", empty=True, limit=128)

    def to_dict(self):
        return {
            "runset": self.runset.to_dict(),
            "dependencies_sha256": self.dependencies_sha256,
            "dependency_count": self.dependency_count,
            "tool_version": self.tool_version,
        }


__all__ = ["CalibreProvenance", "RunsetIdentity"]
