"""Strict, read-only ADE/config and AMS binding contract.

The producer of this payload may be a controller-owned Virtuoso/ADE adapter.
This module only validates and canonicalizes its result; it never launches
ADE, Spectre, Xcelium, or a shell command.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..agent.context import ArtifactLocator
from .assembler_io import payload_artifact, sha256_file

CONFIG_BINDING_SCHEMA_VERSION = "aivw.config.binding.v1"
MAX_BINDING_ITEMS = 4096
MAX_BINDING_BYTES = 256 * 1024
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.$*:+/@=-]{1,256}$")
_DOMAINS = frozenset({"analog", "digital", "ams", "wreal", "electrical"})
_TOP_KEYS = frozenset(
    {
        "schema_version",
        "identity",
        "top",
        "liblist",
        "viewlist",
        "stoplist",
        "cell_bindings",
        "instance_bindings",
        "discipline_bindings",
        "connect_rules",
        "source_artifacts",
    }
)
_IDENTITY_KEYS = frozenset({"library", "cell", "view", "source_generation"})
_TOP_IDENTITY_KEYS = frozenset({"library", "cell", "view"})
_CELL_KEYS = frozenset({"library", "cell", "view"})
_INSTANCE_KEYS = frozenset(
    {
        "parent_library",
        "parent_cell",
        "parent_view",
        "instance",
        "bound_library",
        "bound_cell",
        "bound_view",
    }
)
_DISCIPLINE_KEYS = frozenset({"net", "discipline", "domain"})
_RULE_KEYS = frozenset({"name", "sha256", "artifact"})


class ConfigBindingValidationError(ValueError):
    """Raised when an ADE/config binding is incomplete or unsafe."""


@dataclass(frozen=True)
class ConfigBinding:
    """Canonical immutable-at-boundary config binding."""

    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", validate_config_binding(self.payload))

    @property
    def source_generation(self) -> str:
        return str(self.payload["identity"]["source_generation"])

    @property
    def identity(self) -> Mapping[str, str]:
        return self.payload["identity"]

    def to_dict(self) -> dict[str, Any]:
        return _copy_json(self.payload)


def validate_config_binding(
    value: Mapping[str, Any],
    *,
    target: Mapping[str, Any] | None = None,
    source_generation: str | None = None,
) -> dict[str, Any]:
    """Validate and canonicalize one controller-produced binding payload."""

    root = _object(value, "config_binding")
    unknown = sorted(set(root) - _TOP_KEYS)
    if unknown:
        raise ConfigBindingValidationError(f"config_binding has unknown fields: {unknown}")
    if root.get("schema_version") != CONFIG_BINDING_SCHEMA_VERSION:
        raise ConfigBindingValidationError("unsupported config_binding schema_version")
    identity = _identity(root.get("identity"), "identity")
    top = _identity(root.get("top"), "top", include_generation=False)
    if identity["library"] != top["library"] or identity["cell"] != top["cell"]:
        raise ConfigBindingValidationError("config identity and top identity disagree")
    if target is not None:
        for key in ("library", "cell"):
            if target.get(key) != identity[key]:
                raise ConfigBindingValidationError(f"config target {key} does not match binding")
        if target.get("config_view") is not None and target["config_view"] != identity["view"]:
            raise ConfigBindingValidationError("config target view does not match binding")
    if source_generation is not None and identity["source_generation"] != source_generation:
        raise ConfigBindingValidationError("config source_generation does not match snapshot")
    result: dict[str, Any] = {
        "schema_version": CONFIG_BINDING_SCHEMA_VERSION,
        "identity": identity,
        "top": top,
    }
    result["liblist"] = _string_list(root.get("liblist"), "liblist")
    result["viewlist"] = _string_list(root.get("viewlist"), "viewlist")
    result["stoplist"] = _string_list(root.get("stoplist"), "stoplist")
    if not result["liblist"] or not result["viewlist"] or not result["stoplist"]:
        raise ConfigBindingValidationError("liblist, viewlist, and stoplist must be non-empty")
    result["cell_bindings"] = _cell_bindings(root.get("cell_bindings"))
    result["instance_bindings"] = _instance_bindings(root.get("instance_bindings"))
    result["discipline_bindings"] = _discipline_bindings(root.get("discipline_bindings"))
    result["connect_rules"] = _connect_rules(root.get("connect_rules"))
    result["source_artifacts"] = _source_artifacts(root.get("source_artifacts"))
    encoded = _canonical(result)
    if len(encoded) > MAX_BINDING_BYTES:
        raise ConfigBindingValidationError("config_binding exceeds the bounded JSON limit")
    return json.loads(encoded.decode("utf-8"))


def bind_config_to_target(
    value: Mapping[str, Any],
    *,
    target: Mapping[str, Any],
    source_generation: str,
) -> ConfigBinding:
    """Validate a binding against the authenticated schematic target."""

    return ConfigBinding(
        validate_config_binding(value, target=target, source_generation=source_generation)
    )


def load_config_binding_artifact(
    gate: Mapping[str, Any],
    payload_root: str | Path,
    *,
    target: Mapping[str, Any],
    source_generation: str,
) -> ConfigBinding:
    """Load one indexed, hash-bound controller-produced config artifact.

    The gate may expose only a payload-relative locator and its byte hash.  No
    path supplied by an Agent is accepted, and every source/connect-rule
    locator in the binding is checked against the same payload root.
    """

    locator = config_binding_artifact_locator(gate, payload_root)
    relative = locator.path
    try:
        root = Path(payload_root).expanduser().resolve(strict=True)
        artifact = payload_artifact(root, relative)
        value = json.loads(artifact.read_text(encoding="utf-8"))
    except ConfigBindingValidationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ConfigBindingValidationError(f"config binding artifact is unreadable: {exc}") from exc
    binding = bind_config_to_target(value, target=target, source_generation=source_generation)
    _verify_referenced_artifacts(binding.payload, root)
    return binding


def config_binding_artifact_locator(
    gate: Mapping[str, Any], payload_root: str | Path
) -> ArtifactLocator:
    """Derive the controller-authorized config artifact locator.

    Only the PASS gate output and its artifact index can authorize this
    locator.  This keeps the full binding out of Agent context while allowing
    a later controller/MCP read to retrieve the exact hash-addressed payload.
    """

    if not isinstance(gate, Mapping) or gate.get("status") != "PASS":
        raise ConfigBindingValidationError("config binding gate must be a PASS result")
    if gate.get("executor") != "virtuoso.config_binding":
        raise ConfigBindingValidationError("config binding gate is not authoritative")
    outputs = gate.get("outputs")
    if not isinstance(outputs, Mapping):
        raise ConfigBindingValidationError("config binding gate has no authoritative outputs")
    relative = outputs.get("binding_artifact")
    expected = outputs.get("binding_sha256")
    if not isinstance(relative, str) or not relative:
        raise ConfigBindingValidationError("config binding artifact path is missing")
    if not isinstance(expected, str) or _DIGEST.fullmatch(expected) is None:
        raise ConfigBindingValidationError("config binding artifact hash is invalid")
    indexed: set[str] = set()
    for item in gate.get("artifacts", ()):
        if isinstance(item, str):
            indexed.add(item)
    for item in gate.get("artifact_locators", ()):
        if isinstance(item, Mapping) and isinstance(item.get("path"), str):
            indexed.add(item["path"])
    if relative not in indexed:
        raise ConfigBindingValidationError("config binding artifact is not indexed by its gate")
    try:
        root = Path(payload_root).expanduser().resolve(strict=True)
        artifact = payload_artifact(root, relative)
        size = artifact.stat().st_size
        if size > MAX_BINDING_BYTES:
            raise ConfigBindingValidationError("config binding artifact exceeds the bounded size")
        if sha256_file(artifact) != expected:
            raise ConfigBindingValidationError("config binding artifact hash does not match gate")
        return ArtifactLocator(
            relative,
            sha256=expected,
            size=size,
            media_type="application/json",
        )
    except ConfigBindingValidationError:
        raise
    except Exception as exc:
        raise ConfigBindingValidationError(
            f"config binding artifact locator is unsafe or unreadable: {exc}"
        ) from exc


def _verify_referenced_artifacts(value: Mapping[str, Any], root: Path) -> None:
    references = list(value.get("source_artifacts", ()))
    references.extend(
        item["artifact"]
        for item in value.get("connect_rules", ())
        if isinstance(item, Mapping) and isinstance(item.get("artifact"), Mapping)
    )
    for reference in references:
        try:
            ArtifactLocator.from_dict(reference).resolve(root, must_exist=True)
        except Exception as exc:
            raise ConfigBindingValidationError(
                "config binding references an unavailable or changed artifact"
            ) from exc


def _identity(value: Any, field: str, *, include_generation: bool = True) -> dict[str, str]:
    data = _object(value, field)
    allowed = _IDENTITY_KEYS if include_generation else _TOP_IDENTITY_KEYS
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ConfigBindingValidationError(f"{field} has unknown fields: {unknown}")
    result = {
        "library": _identifier(data.get("library"), f"{field}.library"),
        "cell": _identifier(data.get("cell"), f"{field}.cell"),
        "view": _identifier(data.get("view"), f"{field}.view"),
    }
    if include_generation:
        generation = data.get("source_generation")
        if not isinstance(generation, str) or _DIGEST.fullmatch(generation) is None:
            raise ConfigBindingValidationError(f"{field}.source_generation is invalid")
        result["source_generation"] = generation
    return result


def _cell_bindings(value: Any) -> list[dict[str, str]]:
    rows = _objects(value, "cell_bindings")
    result = []
    for index, row in enumerate(rows):
        _check_keys(row, _CELL_KEYS, f"cell_bindings[{index}]")
        result.append({key: _identifier(row.get(key), f"cell_bindings[{index}].{key}") for key in sorted(_CELL_KEYS)})
    return _unique(result, ("library", "cell", "view"), "cell_bindings")


def _instance_bindings(value: Any) -> list[dict[str, str]]:
    rows = _objects(value, "instance_bindings")
    result = []
    for index, row in enumerate(rows):
        _check_keys(row, _INSTANCE_KEYS, f"instance_bindings[{index}]")
        result.append({key: _identifier(row.get(key), f"instance_bindings[{index}].{key}") for key in sorted(_INSTANCE_KEYS)})
    return _unique(result, tuple(sorted(_INSTANCE_KEYS)), "instance_bindings")


def _discipline_bindings(value: Any) -> list[dict[str, str]]:
    rows = _objects(value, "discipline_bindings")
    result = []
    for index, row in enumerate(rows):
        _check_keys(row, _DISCIPLINE_KEYS, f"discipline_bindings[{index}]")
        domain = row.get("domain")
        if domain not in _DOMAINS:
            raise ConfigBindingValidationError(f"discipline_bindings[{index}].domain is invalid")
        result.append({
            "net": _identifier(row.get("net"), f"discipline_bindings[{index}].net"),
            "discipline": _identifier(row.get("discipline"), f"discipline_bindings[{index}].discipline"),
            "domain": domain,
        })
    return _unique(result, ("net",), "discipline_bindings")


def _connect_rules(value: Any) -> list[dict[str, Any]]:
    rows = _objects(value, "connect_rules")
    result = []
    for index, row in enumerate(rows):
        _check_keys(row, _RULE_KEYS, f"connect_rules[{index}]")
        name = _identifier(row.get("name"), f"connect_rules[{index}].name")
        digest = row.get("sha256")
        if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
            raise ConfigBindingValidationError(f"connect_rules[{index}].sha256 is invalid")
        artifact = row.get("artifact")
        try:
            locator = ArtifactLocator.from_dict(artifact)
        except Exception as exc:
            raise ConfigBindingValidationError(f"connect_rules[{index}].artifact is invalid") from exc
        if locator.sha256 != digest:
            raise ConfigBindingValidationError(f"connect_rules[{index}] hash disagrees with artifact")
        result.append({"name": name, "sha256": digest, "artifact": locator.to_dict()})
    return _unique(result, ("name",), "connect_rules")


def _source_artifacts(value: Any) -> list[dict[str, Any]]:
    rows = _objects(value, "source_artifacts")
    result = []
    for index, row in enumerate(rows):
        try:
            result.append(ArtifactLocator.from_dict(row).to_dict())
        except Exception as exc:
            raise ConfigBindingValidationError(f"source_artifacts[{index}] is invalid") from exc
    return _unique(result, ("path",), "source_artifacts")


def _objects(value: Any, field: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, list) or len(value) > MAX_BINDING_ITEMS:
        raise ConfigBindingValidationError(f"{field} must be a bounded array")
    if any(not isinstance(item, Mapping) for item in value):
        raise ConfigBindingValidationError(f"{field} must contain objects")
    return list(value)


def _string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_BINDING_ITEMS:
        raise ConfigBindingValidationError(f"{field} must be a bounded string array")
    result = [_identifier(item, f"{field}[{index}]") for index, item in enumerate(value)]
    if len(result) != len(set(result)):
        raise ConfigBindingValidationError(f"{field} contains duplicates")
    return result


def _unique(rows: list[dict[str, Any]], fields: tuple[str, ...], field: str) -> list[dict[str, Any]]:
    seen: set[tuple[str, ...]] = set()
    for row in rows:
        key = tuple(str(row.get(item)) for item in fields)
        if key in seen:
            raise ConfigBindingValidationError(f"{field} contains duplicates")
        seen.add(key)
    return sorted(rows, key=lambda row: tuple(str(row.get(item)) for item in fields))


def _check_keys(value: Mapping[str, Any], allowed: frozenset[str], field: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ConfigBindingValidationError(f"{field} has unknown fields: {unknown}")


def _object(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigBindingValidationError(f"{field} must be an object")
    return value


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ConfigBindingValidationError(f"{field} is invalid")
    return value


def _canonical(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ConfigBindingValidationError(f"config_binding is not JSON-shaped: {exc}") from exc


def _copy_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _copy_json(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_copy_json(child) for child in value]
    return value


__all__ = [
    "CONFIG_BINDING_SCHEMA_VERSION",
    "ConfigBinding",
    "ConfigBindingValidationError",
    "bind_config_to_target",
    "config_binding_artifact_locator",
    "load_config_binding_artifact",
    "validate_config_binding",
]
