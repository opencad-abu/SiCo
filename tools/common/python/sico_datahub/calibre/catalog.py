"""Non-authoritative DRC rule catalog returned by static Calibre discovery."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Tuple

from ..codec import ArtifactRef
from ..errors import HubError
from ._validation import artifact, count, digest, text


@dataclass(frozen=True)
class RuleCatalog:
    runset: ArtifactRef
    dependencies_sha256: str
    groups: Tuple[str, ...] = ()
    counts: Mapping[str, int] = field(default_factory=dict)
    members: Mapping[str, Tuple[str, ...]] = field(default_factory=dict)
    layers: Tuple[str, ...] = ()
    source: str = "static"
    error: str = ""
    tool_version: str = ""

    def __post_init__(self):
        artifact(self.runset, "runset")
        digest(self.dependencies_sha256)
        if not isinstance(self.groups, tuple) or len(self.groups) > 4096:
            raise HubError("invalid_arguments", "Invalid rule groups")
        for name in self.groups:
            text(name, "rule group", limit=512)
        for name, value in self.counts.items():
            text(name, "rule group", limit=512)
            count(value, "rule count")
        for name, values in self.members.items():
            text(name, "rule group", limit=512)
            if not isinstance(values, tuple) or len(values) > 4096:
                raise HubError("invalid_arguments", "Invalid rule members")
            for value in values:
                text(value, "rule member", limit=512)
        if not isinstance(self.layers, tuple) or len(self.layers) > 200:
            raise HubError("invalid_arguments", "Invalid rule layer list")
        text(self.source, "catalog source", limit=32)
        text(self.error, "catalog error", empty=True, limit=4096)
        text(self.tool_version, "tool_version", empty=True, limit=128)

    def to_dict(self):
        return {
            "runset": self.runset.to_dict(),
            "dependencies_sha256": self.dependencies_sha256,
            "groups": list(self.groups),
            "counts": dict(self.counts),
            "members": {name: list(values) for name, values in self.members.items()},
            "layers": list(self.layers),
            "source": self.source,
            "error": self.error,
            "tool_version": self.tool_version,
        }


__all__ = ["RuleCatalog"]
