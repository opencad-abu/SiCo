"""Run-local path aliases for EDA parsers that split quoted whitespace."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .config_context import DesignContext


def command_path(ctx: DesignContext, value: str, tool: str) -> str:
    if not any(char.isspace() for char in value):
        return value
    target = Path(value)
    if not target.is_absolute():
        target = ctx.run_dir / target
    relative = os.path.relpath(target, ctx.run_dir)
    if not any(char.isspace() for char in relative):
        return relative
    # A directory alias preserves the basename, including multi-corner output
    # suffixes. Only a spaced basename itself needs a file alias.
    spaced_name = any(char.isspace() for char in target.name)
    linked = target if spaced_name else target.parent
    digest = hashlib.sha256(str(linked).encode()).hexdigest()[:20]
    alias = ctx.log_dir / (tool + "_paths") / digest
    alias.parent.mkdir(parents=True, exist_ok=True)
    if alias.is_symlink():
        if os.readlink(alias) != str(linked):
            raise ValueError(f"Conflicting {tool} path alias: {alias}")
    elif alias.exists():
        raise ValueError(f"{tool} path alias is not a symlink: {alias}")
    else:
        alias.symlink_to(linked, target_is_directory=not spaced_name)
    result = alias if spaced_name else alias / target.name
    return str(result) if not any(c.isspace() for c in str(result)) else os.path.relpath(result, ctx.run_dir)
