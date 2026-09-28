"""Profile defaults, normalized raw data, and path views."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from cadconfig.paths import PATH_KEYS as _PATH_KEYS
from cadconfig.paths import SELECTION_PATH_KEYS as _SELECTION_FILE_OR_INLINE_KEYS
from cadconfig.paths import resolve_path
from cadconfig.scalars import INTEGER_TEXT_PATHS as _INTEGER_TEXT_PATHS
from .schema import ProfileValue
from .values import _get_path, _positive_integer_text
from cadconfig.booleans import boolean_defaults

def _profile_defaults(flow: str) -> dict[str, ProfileValue]:
    defaults: dict[str, ProfileValue] = boolean_defaults(flow)
    defaults.update(
        {
            "run.queue_name": "",
            "run.server_name": "",
        }
    )
    if flow != "LEF":
        defaults.update(
            {
                "batch.scope": "Single Cell",
                "batch.parallel_cells": "2",
                "batch.tasks": (),
            }
        )
    if flow == "DRC":
        defaults.update(
            {
                "drc.run_mode": "Hier",
                "drc.rule_select_groups": (),
                "drc.rule_select_checks": (),
                "drc.custom_svrf_command": "",
                "runtime.cpus": "1",
            }
        )
    elif flow in {"LVS", "RCE"}:
        defaults.update(
            {
                "lvs.run_mode": "Hier",
                "lvs.recognize_gates": "NONE",
                "lvs.custom_svrf_command": "",
                "runtime.lvs_cpus": "1",
            }
        )
        if flow == "LVS":
            defaults["lvs.svdb_query"] = ()
    if flow == "RCE":
        defaults.update(
            {
                "runtime.ext_cpus": "1",
                "extract.corner_scope": "Single Corner",
                "extract.corners": (),
                "extract.corner_temperatures": (),
            }
        )
    elif flow == "LEF":
        defaults.update(
            {
                "run.cpus": "1",
            }
        )
    return defaults


def _normalization_overrides(
    raw: Mapping[str, Any], flow: str, source: Path
) -> dict[str, ProfileValue]:
    overrides: dict[str, ProfileValue] = {
        "profile.path": str(source),
    }
    run = raw.get("run", {})
    if "run_type" not in run:
        overrides["run.run_type"] = "Current Host"

    if flow != "LEF":
        overrides["input.type"] = str(_get_path(raw, "input.type") or "OA")

    if flow == "RCE":
        if _get_path(raw, "extract.corner_scope") is None:
            corners = _get_path(raw, "extract.corners")
            overrides["extract.corner_scope"] = (
                "Multiple Corners" if isinstance(corners, list) and len(corners) > 1
                else "Single Corner"
            )

    for path in _INTEGER_TEXT_PATHS:
        value = _get_path(raw, path)
        if value is not None:
            overrides[path] = _positive_integer_text(value, path)

    return overrides


def _set_path(raw: dict[str, Any], path: str, value: Any) -> None:
    node = raw
    components = path.split(".")
    for component in components[:-1]:
        child = node.get(component)
        if not isinstance(child, dict):
            child = {}
            node[component] = child
        node = child
    node[components[-1]] = value


def _normalized_raw(
    raw: Mapping[str, Any], overrides: Mapping[str, ProfileValue]
) -> dict[str, Any]:
    normalized = deepcopy(dict(raw))
    for path in (
        "input.type",
        "run.run_type",
        "extract.corner_scope",
        "extract.top_cell_source",
        "extract.name_source",
        "extract.view.kind",
        *_INTEGER_TEXT_PATHS,
    ):
        if path in overrides and (
            path == "extract.corner_scope" or _get_path(raw, path) is not None
        ):
            _set_path(normalized, path, overrides[path])
    return normalized


def _path_views(
    entries: Mapping[str, ProfileValue], source: Path, flow: str
) -> dict[str, ProfileValue]:
    views: dict[str, ProfileValue] = {}
    for path in _PATH_KEYS:
        value = entries.get(path)
        if not isinstance(value, str):
            continue
        views[f"{path}.raw"] = value
        if not value:
            views[f"{path}.resolved"] = ""
            continue
        try:
            views[f"{path}.resolved"] = str(resolve_path(value, source.parent))
        except ValueError:
            # Portable profiles retain unresolved variables until execution.
            views[f"{path}.resolved"] = ""

    for path in _SELECTION_FILE_OR_INLINE_KEYS:
        value = entries.get(path)
        if not isinstance(value, str) or not value:
            continue
        try:
            target = resolve_path(value, source.parent)
        except ValueError:
            continue
        if target.is_file():
            views[f"{path}.raw"] = value
            views[f"{path}.resolved"] = str(target)

    return views
