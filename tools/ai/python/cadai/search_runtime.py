"""Resolve CAD AI's shared, pinned ripgrep runtime independently of PATH."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from pathlib import Path

from .installation import runtime_root

RG_RUNTIME_RELATIVE = Path("runtime/ripgrep/x86_64-unknown-linux-musl/bin/rg")


def resolve_rg(
    environment: Mapping[str, str] | None = None,
    *,
    install_roots: Sequence[Path | str] | None = None,
) -> Path:
    source = os.environ if environment is None else environment
    root = runtime_root(source, install_roots or ())
    candidate = root / RG_RUNTIME_RELATIVE
    if candidate.resolve().is_relative_to(root) and candidate.is_file() and os.access(candidate, os.X_OK):
        return candidate.resolve()
    raise FileNotFoundError(
        "Bundled ripgrep is unavailable; restore runtime/ripgrep from the CAD AI release."
    )


def prepare_search_environment(environment: dict[str, str]) -> None:
    """Make the same bundled rg available to both managed terminal clients."""
    directory = str(resolve_rg(environment).parent)
    existing = (environment.get("PATH") or os.defpath).split(os.pathsep)
    environment["PATH"] = os.pathsep.join(
        [directory, *(entry for entry in existing if entry and entry != directory)]
    )
