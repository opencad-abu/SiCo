"""Read the DRC/LVS/stream layer map used by generated fill decks."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


class LayerMapError(ValueError):
    """A stream layer map cannot satisfy the requested layer lookup."""


@dataclass(frozen=True)
class LayerMapEntry:
    name: str
    purpose: str
    stream: int
    datatype: int


@dataclass(frozen=True)
class LayerMap:
    entries: Mapping[tuple[str, str], LayerMapEntry]

    def require(self, name: str, purpose: str = "drawing") -> LayerMapEntry:
        entry = self.entries.get((name, purpose))
        if entry is None:
            raise LayerMapError(
                f"layer map has no {name}/{purpose} entry"
            )
        return entry


def load_layer_map(path: str | Path) -> LayerMap:
    source = Path(path).expanduser().resolve()
    try:
        lines = source.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        raise LayerMapError(f"cannot read layer map {source}: {exc}") from exc
    entries: dict[tuple[str, str], LayerMapEntry] = {}
    for line_number, line in enumerate(lines, 1):
        text = line.split("#", 1)[0].strip()
        fields = text.split()
        if not fields:
            continue
        if len(fields) != 4:
            continue
        name, purpose, stream_text, datatype_text = fields
        try:
            stream, datatype = int(stream_text), int(datatype_text)
        except ValueError:
            continue
        key = (name, purpose)
        if key in entries:
            raise LayerMapError(
                f"duplicate layer map entry {name}/{purpose} at {source}:{line_number}"
            )
        entries[key] = LayerMapEntry(name, purpose, stream, datatype)
    if not entries:
        raise LayerMapError(f"layer map contains no usable entries: {source}")
    return LayerMap(entries)
