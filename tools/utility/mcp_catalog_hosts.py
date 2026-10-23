"""Offline assembly of the catalog layers the MCP report measures.

Three layers exist today and are reported separately:

- ``canonical``: the ordered domain definitions in ``cadai.mcp_definitions``
  (the schema authority for every domain tool).
- ``canonical_wire``: the four MCP fields of the same definitions, i.e. what
  the cadai-managed MCP servers put on the wire.
- ``host``: the SiCo project-service registry assembled from
  ``context_tools`` plus the workbench tools, projected through the real
  ``ToolBridge.dispatch`` path.  This mirrors ``create_backend`` assembly
  without starting a service, opening a session or touching Virtuoso.

Exposure entries record what each current consumer actually advertises:
the SiCo desktop applies no tool filter today (``sico.codex.runtime`` sets no
``enabled_tools``), while the managed-agent launcher restricts the canonical
names to one profile (``cadai.codex_launch``).  Nothing here decides policy;
the report only measures the layers as they exist in the checked-out source.
"""

from __future__ import annotations

import sys
from pathlib import Path
from threading import RLock
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOTS = tuple(
    ROOT / part for part in ("tools/ai/python", "tools/sico/python", "tools/common/python")
)


def ensure_source_paths() -> None:
    """Import SiCo/cadai from this checkout, never from an installed build."""
    for path in reversed(SOURCE_ROOTS):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))


def canonical_definitions() -> list[dict[str, Any]]:
    """The ordered canonical definitions, copied so callers cannot mutate them."""
    ensure_source_paths()
    from cadai import mcp_definitions

    return [dict(row) for row in mcp_definitions.TOOLS]


def canonical_wire_rows() -> list[dict[str, Any]]:
    """The canonical MCP wire projection, including compact circuit schemas."""
    ensure_source_paths()
    from cadai.mcp_registry import wire_row

    return [wire_row(row) for row in canonical_definitions()]


def canonical_module() -> str:
    ensure_source_paths()
    from cadai import mcp_definitions

    return str(mcp_definitions.__file__)


def host_rows() -> list[dict[str, Any]]:
    """Assemble the SiCo host registry offline and project it as MCP wire rows."""
    ensure_source_paths()
    from sico.adapters.virtuoso import context_tools
    from sico.codex.mcp import ToolBridge
    from sico.core.backend_support import CODEX_SUPPORT
    from sico.service.workbench import attach_workbench

    # A deterministic placeholder project root; assembling the registry only
    # derives paths from it and performs no filesystem writes.
    state = ROOT / ".tmp-mcp-catalog" / "project" / ".sico/ai/agent"
    journal = SimpleNamespace(
        root=state,
        directory=state / "sessions/catalog-report",
        session_id="catalog-report",
        events=lambda: [],
    )
    broker = SimpleNamespace(reconcile_binding_operation=lambda *args: None)
    registry = context_tools(broker, journal)
    attach_workbench(SimpleNamespace(tools=registry, journal=journal, lock=RLock(),
                                     support=CODEX_SUPPORT,
                                     state=SimpleNamespace(task={}), _event=lambda *args: None))
    bridge = ToolBridge(registry, lambda *args, **kwargs: None)
    try:
        return bridge.dispatch({"method": "list"})["tools"]
    finally:
        bridge.close()


def host_module() -> str:
    ensure_source_paths()
    from sico.adapters.virtuoso import context_tools

    return str(sys.modules[context_tools.__module__].__file__)


def managed_agent_exposure(profile: str) -> list[str]:
    """The ``enabled_tools`` list the managed-agent launcher writes for one profile."""
    ensure_source_paths()
    from cadai.agent_profile import allowed_tool_names
    from cadai.launch_policy import MCP_TOOL_NAMES

    return list(allowed_tool_names(profile, MCP_TOOL_NAMES))


def managed_agent_profiles() -> tuple[str, ...]:
    ensure_source_paths()
    from cadai.agent_profile import SUPPORTED_PROFILES

    return tuple(sorted(SUPPORTED_PROFILES))


def task_group_declarations() -> dict[str, dict]:
    """The merged runtime task-group declarations (cadai plus the SiCo host)."""
    ensure_source_paths()
    from sico.adapters.task_groups import TASK_GROUPS

    return {group_id: dict(row) for group_id, row in TASK_GROUPS.items()}


__all__ = [
    "ROOT",
    "SOURCE_ROOTS",
    "ensure_source_paths",
    "canonical_definitions",
    "canonical_module",
    "host_rows",
    "host_module",
    "managed_agent_exposure",
    "managed_agent_profiles",
    "task_group_declarations",
]
