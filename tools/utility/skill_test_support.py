"""Read declared SKILL source groups for source-contract tests only."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def rce_source_paths(group: str) -> tuple[Path, ...]:
    return skill_group_paths("rce", group)


def skill_group_paths(scope: str, group: str) -> tuple[Path, ...]:
    inventory = json.loads((ROOT.parent / f"deploy/{scope}_{group}_sources.json").read_text())
    return tuple(ROOT.parent / relative for relative in inventory["sources"])


def rce_callback_paths() -> tuple[Path, ...]:
    return rce_source_paths("callback")


def read_skill_source(path: Path) -> str:
    """Read a source or the explicitly inventoried RCE callback definitions."""
    groups = {
        "rce/skill++/RCECB.ils": ("rce", "callback"),
        "rce/skill++/RCEBASEGUI.ils": ("rce", "gui"),
        "rce/skill/UI_rceSummary.il": ("rce", "summary"),
        "common/skill++/BASEGUI.ils": ("common", "gui"),
        "common/skill/SICO_lsf.il": ("common", "lsf"),
    }
    for relative, (scope, group) in groups.items():
        if path.resolve() == (ROOT / relative).resolve():
            return "\n".join(
                source.read_text(encoding="utf-8") for source in skill_group_paths(scope, group)
            )
    return path.read_text(encoding="utf-8")


def rce_callback_loads() -> str:
    """Emit source loads for opt-in development probes, never runtime packaging."""
    return rce_source_loads("callback")


def rce_source_loads(group: str) -> str:
    return "\n".join(f'load("{path}")' for path in rce_source_paths(group))


def common_source_loads(group: str) -> str:
    return "\n".join(f'load("{path}")' for path in skill_group_paths("common", group))
