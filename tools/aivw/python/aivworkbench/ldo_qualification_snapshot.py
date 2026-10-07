"""Collect and revalidate a bounded source snapshot."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from .ldo_qualification_cdslib import (
    qualification_allowed_cds_path,
    qualification_parse_cds_lib,
)
from .ldo_qualification_models import qualification_model_identity
from .ldo_qualification_paths import (
    qualification_external_file_record,
    qualification_file_record,
    qualification_has_symlink_below,
    qualification_safe_child,
    qualification_safe_root,
)
from .ldo_qualification_status import (
    LDO_TOPOLOGIES,
    M3_BLOCKED_ENVIRONMENT,
    M3_BLOCKED_INPUT,
    M3_SCHEMA_VERSION,
    M3_STALE_SOURCE,
)
from .ldo_qualification_values import qualification_digest, qualification_now
from .ldo_snapshot_authentication import validate_authenticated_snapshot

_CELL = re.compile(r"^(?:LDO_MASTER|LDO_AON)$")


def build_source_snapshot(
    source_root: str | Path,
    *,
    library: str = "amsLDO",
    cells: Sequence[str] = LDO_TOPOLOGIES,
    cds_lib: str | Path | None = None,
    source_cds_lib: str | Path | None = None,
    mapping_root: str | Path | None = None,
    mapping_allowed_roots: Sequence[str | Path] | None = None,
    mapping_environment: Mapping[str, str] | None = None,
    model_root: str | Path | None = None,
    required_library_mappings: Sequence[str] = ("amsLDO", "gpdk045", "analogLib", "basic"),
    required_model_sections: Sequence[str] = ("tt", "ff", "ss"),
    authenticated_snapshot: Mapping[str, Any] | None = None,
    scope: str = "qualification",
) -> dict[str, Any]:
    """Collect a bounded, read-only source/PDK preflight snapshot."""

    started = qualification_now()
    errors: list[str] = []
    warnings: list[str] = []
    try:
        root = qualification_safe_root(source_root)
    except ValueError as exc:
        return {
            "schema_version": M3_SCHEMA_VERSION,
            "kind": "ldo-source-snapshot",
            "status": M3_BLOCKED_INPUT,
            "authenticated": False,
            "authority": "filesystem-preflight",
            "source_root": str(source_root),
            "library": library,
            "cells": list(cells),
            "source_generation": None,
            "errors": [str(exc)],
            "warnings": [],
            "started_at": started,
            "finished_at": qualification_now(),
        }
    if library != "amsLDO":
        errors.append("M3 LDO source library must be amsLDO")
    selected_cells = tuple(cells)
    if scope == "recipe_target":
        if len(selected_cells) != 1 or selected_cells[0] not in LDO_TOPOLOGIES:
            errors.append("recipe source snapshot must select one approved LDO topology")
    elif scope != "qualification":
        errors.append("unsupported LDO source snapshot scope")
    elif set(selected_cells) != set(LDO_TOPOLOGIES) or len(selected_cells) != len(LDO_TOPOLOGIES):
        errors.append("source snapshot must include exactly LDO_MASTER and LDO_AON")
    views: dict[str, dict[str, Any]] = {}
    relative_views = {
        "schematic": "schematic/sch.oa",
        "symbol": "symbol/symbol.oa",
        "config": "config/expand.cfg",
        "verilogams": "verilogams/verilog.vams",
    }
    for cell in selected_cells:
        if _CELL.fullmatch(str(cell)) is None:
            errors.append("unsupported LDO cell: %s" % cell)
            continue
        cell_dir = root / library / str(cell)
        cell_views: dict[str, Any] = {}
        for view_name, relative in relative_views.items():
            relative_path = "%s/%s/%s" % (library, cell, relative)
            try:
                path = qualification_safe_child(root, relative_path, kind="file")
                cell_views[view_name] = qualification_file_record(path, root)
            except ValueError as exc:
                errors.append(str(exc))
                cell_views[view_name] = {"path": relative_path, "missing": True}
        views[str(cell)] = cell_views
        if not cell_dir.is_dir():
            errors.append("LDO cell directory is missing: %s" % cell_dir)
    mapping_path = Path(cds_lib).expanduser() if cds_lib is not None else root / "cds.lib"
    source_path = Path(source_cds_lib).expanduser() if source_cds_lib is not None else mapping_path
    mapping_roots = tuple(Path(item).expanduser() for item in (mapping_allowed_roots or ()))
    if mapping_root is not None:
        mapping_roots = (Path(mapping_root).expanduser(), *mapping_roots)
    mapping_roots = tuple(item.resolve(strict=False) for item in mapping_roots) or (root,)
    mapping_valid = (
        mapping_path.is_absolute()
        and not mapping_path.is_symlink()
        and mapping_path.is_file()
        and qualification_allowed_cds_path(mapping_path, mapping_roots)
    )
    source_valid = (
        source_path.is_absolute()
        and not source_path.is_symlink()
        and source_path.is_file()
        and source_path.resolve(strict=False).is_relative_to(root)
    )
    if not mapping_valid:
        errors.append("mapping cds.lib must be a regular file inside approved mapping roots")
        cds_record: dict[str, Any] = {"path": str(mapping_path), "missing": True}
        cds_info: dict[str, Any] = {"definitions": {}, "missing": list(required_library_mappings), "errors": []}
    else:
        mapping_path = mapping_path.resolve()
        cds_record = qualification_external_file_record(mapping_path)
        cds_info = qualification_parse_cds_lib(
            mapping_path,
            root,
            required_library_mappings,
            allowed_roots=mapping_roots,
            environment=mapping_environment,
        )
        errors.extend(str(item) for item in cds_info.get("errors", ()))
    if not source_valid:
        errors.append("source cds.lib must be a regular file inside source root")
        source_record: dict[str, Any] = {"path": str(source_path), "missing": True}
    else:
        source_path = source_path.resolve()
        source_record = qualification_file_record(source_path, root)
    model_path = Path(model_root).expanduser() if model_root is not None else root / "models" / "spectre"
    try:
        model_resolved = model_path.resolve(strict=True)
    except OSError:
        model_resolved = model_path.resolve(strict=False)
    if (
        not model_path.is_absolute()
        or model_path.is_symlink()
        or qualification_has_symlink_below(root, model_resolved)
        or not model_resolved.is_dir()
        or not model_resolved.is_relative_to(root)
    ):
        errors.append("Spectre model root must be an existing directory inside source root")
        model_info: dict[str, Any] = {"root": str(model_path), "files": [], "errors": ["model root unavailable"], "missing_sections": list(required_model_sections)}
    else:
        model_path = model_resolved
        model_info = qualification_model_identity(model_path, root, required_model_sections)
        errors.extend(str(item) for item in model_info.get("errors", ()))
    source_material = {
        "library": library,
        "cells": list(selected_cells),
        "views": views,
        "cds_lib": cds_record,
        "source_cds_lib": source_record,
        "cds_mapping": cds_info,
        "model": model_info,
    }
    source_generation = qualification_digest(source_material) if not any(item.get("missing") for cell in views.values() for item in cell.values() if isinstance(item, Mapping)) else None
    if source_generation is None:
        errors.append("source generation could not be established from complete saved views")
    auth_result: dict[str, Any] = {
        "status": M3_BLOCKED_ENVIRONMENT,
        "authenticated": False,
        "authority": "filesystem-preflight",
        "findings": ["controller-delegated authenticated snapshot was not supplied"],
    }
    if authenticated_snapshot is not None and source_generation is not None:
        auth_result = validate_authenticated_snapshot(
            authenticated_snapshot,
            source_generation=source_generation,
            library=library,
            cells=selected_cells,
        )
        if auth_result["status"] != "PASS":
            errors.extend(str(item) for item in auth_result.get("findings", ()))
    elif authenticated_snapshot is not None:
        errors.append("authenticated snapshot cannot be checked without source generation")
    status = M3_BLOCKED_INPUT if errors else ("PASS" if auth_result["authenticated"] else M3_BLOCKED_ENVIRONMENT)
    snapshot = {
        "schema_version": M3_SCHEMA_VERSION,
        "kind": "ldo-source-snapshot",
        "scope": scope,
        "status": status,
        "authenticated": bool(auth_result.get("authenticated")),
        "authority": auth_result.get("authority", "filesystem-preflight"),
        "source_root": str(root),
        "library": library,
        "cells": list(selected_cells),
        "views": views,
        "cds_lib": {**cds_record, "mapping": cds_info, "source": source_record},
        "pdk": model_info,
        "source_generation": source_generation,
        "preflight_digest": qualification_digest(source_material),
        "formal_saved_state": bool(auth_result.get("authenticated")),
        "double_read_equal": bool(auth_result.get("authenticated")),
        "errors": sorted(set(errors)),
        "warnings": sorted(set(warnings)),
        "started_at": started,
        "finished_at": qualification_now(),
    }
    snapshot["snapshot_digest"] = qualification_digest({key: value for key, value in snapshot.items() if key != "snapshot_digest"})
    return snapshot


def verify_source_snapshot_unchanged(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Re-read indexed source files and detect mutation between gates."""

    if not isinstance(snapshot, Mapping):
        return {"status": M3_BLOCKED_INPUT, "findings": ["source snapshot must be an object"]}
    root_value = snapshot.get("source_root")
    try:
        root = qualification_safe_root(str(root_value))
    except ValueError as exc:
        return {"status": M3_BLOCKED_INPUT, "findings": [str(exc)]}
    before = snapshot.get("views")
    if not isinstance(before, Mapping):
        return {"status": M3_BLOCKED_INPUT, "findings": ["source snapshot has no indexed views"]}
    changed: list[str] = []
    after: dict[str, Any] = {}
    for cell, raw_views in before.items():
        if not isinstance(raw_views, Mapping):
            changed.append(str(cell) + ":malformed")
            continue
        after_views: dict[str, Any] = {}
        for view_name, raw_record in raw_views.items():
            if not isinstance(raw_record, Mapping) or not isinstance(raw_record.get("path"), str):
                changed.append("%s.%s" % (cell, view_name))
                continue
            try:
                path = qualification_safe_child(root, str(raw_record["path"]), kind="file")
                current = qualification_file_record(path, root)
                after_views[str(view_name)] = current
                if current.get("sha256") != raw_record.get("sha256") or current.get("size") != raw_record.get("size"):
                    changed.append("%s.%s" % (cell, view_name))
            except ValueError as exc:
                changed.append("%s.%s:%s" % (cell, view_name, exc))
        after[str(cell)] = after_views
    result = {
        "status": M3_STALE_SOURCE if changed else "PASS",
        "source_generation_before": snapshot.get("source_generation"),
        "source_generation_after": qualification_digest({"views": after}) if not changed else None,
        "changed": sorted(changed),
        "views_after": after,
    }
    return result
