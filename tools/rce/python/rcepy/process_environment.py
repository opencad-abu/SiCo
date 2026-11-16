"""Snapshot process environment inputs into an immutable execution request."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

from cadconfig.values import MISSING, lookup, thaw
from .pathutil import resolve_path


PROCESS_FILES = {
    "RCE_GDS_OBJECT_MAP": "input.layout.object_map",
    "RCE_GDS_PROPERTY_MAP": "input.layout.property_map",
    "RCE_GDS_USER_SKILL_FILE": "input.layout.user_skill_file",
    "RCE_CDL_SOURCE_ADDED_FILE": "input.source_added_file",
    "RCE_LVS_INCLUDE_DIR": "lvs.include_dir",
    "RCE_LVS_EXTRA_FILE": "lvs.extra_file",
    "RCE_STARRC_REFERENCE_FILE": "extract.starrc.reference_file",
    "RCE_QRC_COMMAND_FILE": "extract.qrc.technology_command_file",
    "RCE_QRC_LAYER_SETUP_FILE": "extract.qrc.technology_layer_setup_file",
    "RCE_QRC_LPE_CONFILE": "extract.qrc.technology_lpe_confile",
    "RCE_QUANTUS_BLOCK_CELL_FILE": "extract.qrc.block_cell_file",
}


def process_environment(raw: Mapping[str, Any], base: Path) -> dict[str, Any]:
    result = thaw(raw)

    def put(path: str, value: Any) -> None:
        node = result
        parts = path.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
            if not isinstance(node, dict):
                raise ValueError(f"Execution TOML section {part} must be a table")
        node[parts[-1]] = value

    if lookup(raw, "extract.finfet_flow") is MISSING:
        mode = os.environ.get("RCE_FINFET_FLOW", "0").strip()
        if mode not in {"0", "1"}:
            raise ValueError("RCE_FINFET_FLOW must be 0 or 1")
        put("extract.finfet_flow", mode == "1")
    for env, path in PROCESS_FILES.items():
        if lookup(raw, path) is not MISSING:
            continue
        value = os.environ.get(env, "").strip()
        # Preserve the established technology-relative block-cell contract.
        if value and env != "RCE_QUANTUS_BLOCK_CELL_FILE":
            value = str(resolve_path(value, base))
        put(path, value)
    return result
