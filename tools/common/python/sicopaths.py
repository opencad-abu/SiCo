"""Validate the SiCo installation identity and derive paths inside that installation."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from sicoenv import value


MARKER = Path("etc/config/sico-install.json")
IDENTITY = {"format": "cad.runtime.install.v1", "product": "Silicon Copilot", "layout": 1}
IDENTITY_BYTES = (json.dumps(IDENTITY, separators=(",", ":")) + "\n").encode("ascii")


def _absolute(raw: str | Path, label: str) -> Path:
    if not str(raw).strip():
        raise ValueError(f"{label} must name an absolute SiCo installation")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{label} must name an absolute SiCo installation")
    return path.resolve()


@dataclass(frozen=True)
class Installation:
    """One validated root; tools and resources have no independent root setting."""

    root: Path

    def __post_init__(self):
        root = _absolute(self.root, "installation root")
        object.__setattr__(self, "root", root)
        marker = self.path(MARKER.as_posix())
        try:
            # Bound input before JSON decoding; this is a tiny runtime config.
            with marker.open("rb") as stream:
                encoded = stream.read(1025)
        except OSError:
            encoded = None
        if encoded != IDENTITY_BYTES:
            raise ValueError("Invalid SiCo installation identity: " + str(MARKER))
        for directory in ("bin", "tools", "tools/common"):
            if not self.path(directory).is_dir():
                raise ValueError("Incomplete SiCo installation: " + directory)

    def path(self, relative: str) -> Path:
        """Return a contained path, rejecting traversal and escaping symlinks."""
        path = PurePosixPath(relative)
        if (not relative or path.is_absolute() or ".." in path.parts
                or "\\" in relative or any(ord(char) < 32 for char in relative)):
            raise ValueError("Expected a relative installation path")
        result = self.root.joinpath(*path.parts).resolve()
        if result != self.root and self.root not in result.parents:
            raise ValueError("Path escapes the SiCo installation")
        return result

    @property
    def tools(self) -> Path:
        return self.path("tools")

    def tool(self, name: str) -> Path:
        if not re.fullmatch(r"[a-z][a-z0-9-]*", name):
            raise ValueError("Invalid SiCo tool name")
        return self.path("tools/" + name)

    def icon(self, category: str, name: str) -> Path:
        if category not in {"brand", "actions", "status"}:
            raise ValueError("Invalid SiCo icon category")
        if not re.fullmatch(r"[a-zA-Z0-9_-]+[.]png", name):
            raise ValueError("Invalid SiCo icon name")
        return self.path(f"share/sico/icons/{category}/{name}")

    @property
    def terminal_data(self) -> Path:
        return self.path("share/qtermwidget5")


def installation(
    environment: Mapping[str, str] | None = None,
    *,
    anchor: Path | None = None,
) -> Installation:
    """Resolve an explicit root or the caller's known installation root.

    ``anchor`` is derived by a launcher from its own fixed installed location,
    never from CWD or an ancestor search. A configured root must agree with it.
    CAD_HOME compatibility accepts only the new, marked installation layout.
    Old Agent roots are not product roots and require a configuration migration.
    """
    env = os.environ if environment is None else environment
    configured = value(env, "SICO_HOME", ("CAD_HOME",))
    if configured is None:
        if "SICO_ROOT" in env or "CAD_AGENT_ROOT" in env:
            raise ValueError("Migrate SICO_ROOT/CAD_AGENT_ROOT to an explicit SICO_HOME")
        if anchor is None:
            raise ValueError("Set SICO_HOME to a marked SiCo installation")
        configured = anchor
    selected = Installation(_absolute(configured, "SICO_HOME"))
    if anchor is not None and selected.root != _absolute(anchor, "launcher installation"):
        raise ValueError("SICO_HOME conflicts with the launcher installation")
    return selected
