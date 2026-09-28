"""Read and record immutable defaults for one generation request."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from types import MappingProxyType
from typing import Iterable, Mapping

from sicopaths import installation


@dataclass(frozen=True)
class Source:
    name: str
    path: Path
    data: bytes

    @property
    def text(self) -> str:
        return self.data.decode("utf-8")

    def error(self, line: int, message: str) -> ValueError:
        return ValueError(f"{self.path}:{line}: {message}")


@dataclass(frozen=True)
class Defaults:
    """A request owns one immutable set; later requests read fresh files."""

    sources: Mapping[str, Source]

    @classmethod
    def load(cls, names: Iterable[str]) -> "Defaults":
        installed = installation(anchor=Path(__file__).resolve().parents[4])
        sources = {}
        for name in dict.fromkeys(names):
            if Path(name).name != name:
                raise ValueError(f"Invalid defaults filename: {name}")
            path = installed.path("etc/flow-defaults/" + name)
            try:
                data = path.read_bytes()
                data.decode("utf-8")
            except (OSError, UnicodeError) as exc:
                raise ValueError(f"Cannot read flow defaults {path}: {exc}") from exc
            sources[name] = Source(name, path, data)
        return cls(MappingProxyType(sources))

    def source(self, name: str) -> Source:
        try:
            return self.sources[name]
        except KeyError as exc:
            raise ValueError(f"Defaults request did not load {name}") from exc

    def save(self, log_dir: Path) -> None:
        directory = log_dir / "flow-defaults"
        directory.mkdir(parents=True, exist_ok=True)
        for source in self.sources.values():
            (directory / source.name).write_bytes(source.data)
            record = {
                "format": "sico.flow-default-source.v1",
                "relative_path": "etc/flow-defaults/" + source.name,
                "source_path": str(source.path),
                "sha256": sha256(source.data).hexdigest(),
            }
            (directory / (source.name + ".json")).write_text(
                json.dumps(record, indent=2) + "\n", encoding="utf-8"
            )


def resolve_defaults(
    names: Iterable[str], defaults: Defaults | None, log_dir: Path | None = None,
) -> Defaults:
    if defaults is not None:
        for name in names:
            defaults.source(name)
        return defaults
    loaded = Defaults.load(names)
    if log_dir is not None:
        loaded.save(log_dir)
    return loaded
