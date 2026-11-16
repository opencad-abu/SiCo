"""Resolve process companion files without modifying foundry inputs."""

from __future__ import annotations

from pathlib import Path

from .config import RceConfig


def optional_input_file(cfg: RceConfig, *keys: str) -> str:
    path = cfg.path(*keys)
    if path and not Path(path).is_file():
        raise FileNotFoundError(f"Cannot access {'.'.join(keys)}: {path}")
    return path


def existing_input_file(cfg: RceConfig, *keys: str) -> str:
    """Return an optional input only when its selected path is a regular file."""
    try:
        path = cfg.path(*keys)
    except ValueError:
        return ""
    return path if path and Path(path).is_file() else ""


def companion_file(
    cfg: RceConfig, keys: tuple[str, ...], roots: tuple[Path, ...], name: str,
) -> Path:
    explicit = optional_input_file(cfg, *keys)
    if explicit:
        return Path(explicit)
    for root in roots:
        path = root / name
        if path.is_file():
            return path.resolve()
    raise FileNotFoundError(
        f"FinFET extraction requires {name}; set {'.'.join(keys)} or place it "
        f"in the process directory ({', '.join(str(root) for root in roots)})"
    )
