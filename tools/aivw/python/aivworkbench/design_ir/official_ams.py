"""Convert qualified HED/dbAccess/runams evidence into config binding IR.

This adapter is intentionally offline.  A controller-owned producer may feed
it a normalized official AMS structure, but the adapter never launches
Virtuoso, ADE, ``runams``, or a shell process and never accepts paths outside
the current payload root.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ..agent.context import ArtifactLocator
from .assembler_io import payload_artifact, sha256_file
from .config_binding import (
    ConfigBinding,
    ConfigBindingValidationError,
    bind_config_to_target,
)


class OfficialAMSBindingError(ValueError):
    """Raised when official AMS evidence cannot establish a complete binding."""


def build_config_binding_from_official_structure(
    normalized: Mapping[str, Any],
    *,
    target: Mapping[str, Any],
    source_generation: str,
    payload_root: str | Path,
    source_root: str | Path,
    evidence: Mapping[str, Any] | None = None,
    discipline_bindings: Sequence[Mapping[str, Any]],
    connect_rules: Sequence[Mapping[str, Any]],
) -> ConfigBinding:
    """Build a target-bound config binding from qualified official evidence.

    ``normalized`` follows the existing M0-S normalized structure shape.  The
    source paths emitted by its ``ams_unl.sources`` map are relative to
    ``source_root``; they are converted to hash-addressed payload locators so
    the resulting binding remains portable and auditable.
    """

    root = _mapping(normalized, "normalized structure")
    _require(root.get("schema_version") == 1, "unsupported normalized structure schema")
    target_obj = _target(target)
    _verify_official_evidence(root, evidence)

    top = _mapping(root.get("top"), "normalized.top")
    if (
        top.get("library") != target_obj["library"]
        or top.get("cell") != target_obj["cell"]
    ):
        raise OfficialAMSBindingError("official AMS top does not match recipe target")
    top_view = _text(top.get("view"), "normalized.top.view")
    config_view = _text(target_obj.get("config_view"), "target.config_view")

    defaults = _mapping(root.get("defaults"), "normalized.defaults")
    ams = _mapping(root.get("ams_unl"), "normalized.ams_unl")
    cell_bindings = _cell_bindings(ams.get("bindings"))
    instance_bindings = _instance_bindings(ams.get("instance_bindings"))
    if not cell_bindings:
        raise OfficialAMSBindingError("official AMS evidence contains no cell bindings")

    payload = _path(payload_root, "payload_root")
    sources = _source_artifacts(ams.get("sources"), source_root, payload)
    if not sources:
        raise OfficialAMSBindingError("official AMS evidence contains no source artifacts")
    disciplines = _sequence(discipline_bindings, "discipline_bindings")
    rules = _connect_rules(connect_rules, payload)
    if not disciplines:
        raise OfficialAMSBindingError("discipline/domain evidence is required")
    if not rules:
        raise OfficialAMSBindingError("connect-rule evidence is required")

    binding = {
        "schema_version": "aivw.config.binding.v1",
        "identity": {
            "library": target_obj["library"],
            "cell": target_obj["cell"],
            "view": config_view,
            "source_generation": source_generation,
        },
        "top": {
            "library": target_obj["library"],
            "cell": target_obj["cell"],
            "view": top_view,
        },
        "liblist": _strings(defaults.get("liblist"), "normalized.defaults.liblist"),
        "viewlist": _strings(defaults.get("viewlist"), "normalized.defaults.viewlist"),
        "stoplist": _strings(defaults.get("stoplist"), "normalized.defaults.stoplist"),
        "cell_bindings": cell_bindings,
        "instance_bindings": instance_bindings,
        "discipline_bindings": [dict(item) for item in disciplines],
        "connect_rules": [dict(item) for item in rules],
        "source_artifacts": sources,
    }
    try:
        return bind_config_to_target(
            binding,
            target=target_obj,
            source_generation=source_generation,
        )
    except ConfigBindingValidationError as exc:
        raise OfficialAMSBindingError(f"official AMS binding is invalid: {exc}") from exc


def _verify_official_evidence(
    root: Mapping[str, Any], supplied: Mapping[str, Any] | None
) -> None:
    evidence = _mapping(
        supplied if supplied is not None else root.get("evidence"),
        "normalized.evidence",
    )
    required_true = (
        "top_matches",
        "hed_traversal_ok",
        "catalog_authoritative",
        "catalog_top_opened",
        "explicit_instance_bindings_accounted",
        "success_marker",
    )
    for field in required_true:
        if evidence.get(field) is not True:
            raise OfficialAMSBindingError(f"official AMS evidence does not prove {field}")
    if evidence.get("fatal_log_marker") is not False:
        raise OfficialAMSBindingError("official AMS log contains a fatal marker")
    if evidence.get("unresolved_count") != 0:
        raise OfficialAMSBindingError("official AMS evidence contains unresolved bindings")
    if evidence.get("netlist_status") != "success":
        raise OfficialAMSBindingError("official AMS netlist status is not success")
    missing = evidence.get("missing_artifacts")
    if not isinstance(missing, list) or missing:
        raise OfficialAMSBindingError("official AMS evidence has missing artifacts")
    ams = _mapping(root.get("ams_unl"), "normalized.ams_unl")
    if ams.get("provider") != "runams/AMS Unified Netlister":
        raise OfficialAMSBindingError("normalized structure is not official runams evidence")
    hed = _mapping(root.get("hed"), "normalized.hed")
    if hed.get("provider") != "Virtuoso/HED" or hed.get("config_opened") is not True:
        raise OfficialAMSBindingError("HED evidence is not authoritative")
    catalog = _mapping(root.get("oa_catalog"), "normalized.oa_catalog")
    if catalog.get("provider") != "dbAccess" or catalog.get("authoritative") is not True:
        raise OfficialAMSBindingError("dbAccess catalog evidence is not authoritative")


def _source_artifacts(value: Any, source_root: Path | str, payload_root: Path) -> list[dict[str, Any]]:
    rows = _mapping(value, "normalized.ams_unl.sources")
    root = _path(source_root, "source_root")
    result: list[dict[str, Any]] = []
    for key, raw in sorted(rows.items(), key=lambda item: str(item[0])):
        item = _mapping(raw, f"normalized.ams_unl.sources[{key}]")
        relative = _text(item.get("path"), f"normalized.ams_unl.sources[{key}].path")
        try:
            source = payload_artifact(root, relative)
            payload_relative = source.relative_to(payload_root).as_posix()
        except (OSError, ValueError) as exc:
            raise OfficialAMSBindingError(
                f"official source artifact is outside payload or unreadable: {relative}"
            ) from exc
        digest = sha256_file(source)
        size = source.stat().st_size
        if ("sha256" in item or "size" in item) and (
            item.get("sha256") != digest or type(item.get("size")) is not int
            or item["size"] != size
        ):
            raise OfficialAMSBindingError("official source artifact changed after normalization")
        result.append(
            ArtifactLocator(
                payload_relative,
                sha256=digest,
                size=size,
                media_type="application/octet-stream",
            ).to_dict()
        )
    return _unique(result, "source_artifacts")


def _connect_rules(value: Any, payload_root: Path) -> list[dict[str, Any]]:
    rows = _sequence(value, "connect_rules")
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(rows):
        row = _mapping(raw, f"connect_rules[{index}]")
        name = _text(row.get("name"), f"connect_rules[{index}].name")
        digest = _text(row.get("sha256"), f"connect_rules[{index}].sha256")
        artifact = _mapping(row.get("artifact"), f"connect_rules[{index}].artifact")
        try:
            locator = ArtifactLocator.from_dict(artifact)
            path = locator.resolve(payload_root, must_exist=True)
        except Exception as exc:
            raise OfficialAMSBindingError(
                f"connect_rules[{index}] artifact is unavailable or unsafe"
            ) from exc
        if not locator.media_type:
            raise OfficialAMSBindingError(
                f"connect_rules[{index}] artifact media_type is required"
            )
        if locator.sha256 != digest or sha256_file(path) != digest:
            raise OfficialAMSBindingError(f"connect_rules[{index}] hash does not match payload")
        if locator.size != path.stat().st_size:
            raise OfficialAMSBindingError(f"connect_rules[{index}] size does not match payload")
        result.append({"name": name, "sha256": digest, "artifact": locator.to_dict()})
    return _unique(result, "connect_rules")


def _cell_bindings(value: Any) -> list[dict[str, str]]:
    rows = _sequence(value, "normalized.ams_unl.bindings")
    result = []
    for index, item in enumerate(rows):
        row = _mapping(item, f"normalized.ams_unl.bindings[{index}]")
        result.append(
            {
                "library": _text(row.get("library"), f"cell_bindings[{index}].library"),
                "cell": _text(row.get("cell"), f"cell_bindings[{index}].cell"),
                "view": _text(row.get("view"), f"cell_bindings[{index}].view"),
            }
        )
    return _unique(result, "cell_bindings")


def _instance_bindings(value: Any) -> list[dict[str, str]]:
    rows = _sequence(value, "normalized.ams_unl.instance_bindings")
    fields = (
        "parent_library",
        "parent_cell",
        "parent_view",
        "instance",
        "bound_library",
        "bound_cell",
        "bound_view",
    )
    result = []
    for index, item in enumerate(rows):
        row = _mapping(item, f"normalized.ams_unl.instance_bindings[{index}]")
        result.append({field: _text(row.get(field), f"instance_bindings[{index}].{field}") for field in fields})
    return _unique(result, "instance_bindings")


def _target(value: Mapping[str, Any]) -> dict[str, str]:
    library = _text(value.get("library"), "target.library")
    cell = _text(value.get("cell"), "target.cell")
    config_view = _text(value.get("config_view"), "target.config_view")
    return {"library": library, "cell": cell, "config_view": config_view}


def _strings(value: Any, field: str) -> list[str]:
    rows = _sequence(value, field)
    result = [_text(item, f"{field}[{index}]") for index, item in enumerate(rows)]
    if not result:
        raise OfficialAMSBindingError(f"{field} must not be empty")
    if len(result) != len(set(result)):
        raise OfficialAMSBindingError(f"{field} contains duplicates")
    return result


def _sequence(value: Any, field: str) -> list[Any]:
    if not isinstance(value, (list, tuple)):
        raise OfficialAMSBindingError(f"{field} must be an array")
    return list(value)


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise OfficialAMSBindingError(f"{field} must be an object")
    return value


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise OfficialAMSBindingError(f"{field} must be a non-empty string")
    return value


def _path(value: str | Path, field: str) -> Path:
    path = Path(value).expanduser()
    if path.is_symlink() or not path.is_dir():
        raise OfficialAMSBindingError(f"{field} must be a real directory")
    try:
        return path.resolve(strict=True)
    except OSError as exc:
        raise OfficialAMSBindingError(f"{field} cannot be resolved") from exc


def _unique(rows: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    keys = [tuple(sorted((str(key), str(value)) for key, value in row.items())) for row in rows]
    if len(keys) != len(set(keys)):
        raise OfficialAMSBindingError(f"{field} contains duplicates")
    return sorted(rows, key=lambda row: tuple(str(item) for item in row.values()))


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise OfficialAMSBindingError(message)


__all__ = [
    "OfficialAMSBindingError",
    "build_config_binding_from_official_structure",
]
