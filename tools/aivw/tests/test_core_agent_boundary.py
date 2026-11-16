"""Core modules must not import the retired agent package.

The agent stack was retired in favor of the SiCo agent base (see
docs/AGENT_RETIREMENT_PLAN.md).  No core module may import
``aivworkbench.agent`` again.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path


PACKAGE = Path(__file__).resolve().parents[1] / "python" / "aivworkbench"

LEGACY_DRIVERS: frozenset[str] = frozenset()


def _is_agent_module(target: str) -> bool:
    return target == "aivworkbench.agent" or target.startswith("aivworkbench.agent.")


def _agent_imports(path: Path) -> list[str]:
    relative = path.relative_to(PACKAGE)
    package = "aivworkbench"
    if relative.parts[:-1]:
        package += "." + ".".join(relative.parts[:-1])
    violations: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
        targets: list[str] = []
        if isinstance(node, ast.ImportFrom):
            target = "." * node.level + (node.module or "")
            try:
                targets.append(importlib.util.resolve_name(target, package))
            except (ImportError, ValueError):
                targets.append(target)
        elif isinstance(node, ast.Import):
            targets.extend(alias.name for alias in node.names)
        for target in targets:
            if _is_agent_module(target):
                violations.append(f"{relative}:{node.lineno}:{target}")
    return violations


def test_core_does_not_import_the_retiring_agent_package():
    violations: list[str] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        relative = path.relative_to(PACKAGE)
        if "__pycache__" in relative.parts or relative.parts[0] == "agent":
            continue
        if relative.as_posix() in LEGACY_DRIVERS:
            continue
        violations.extend(_agent_imports(path))
    assert violations == []
