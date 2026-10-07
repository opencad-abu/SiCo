"""Build bounded DesignIR/context from an authenticated Virtuoso snapshot.

The snapshot executor is the authority for saved-state identity.  This module
does not inspect arbitrary files and does not infer identity from a summary:
the caller must provide the serialized ``virtuoso.snapshot`` gate outputs and
the payload root owned by the current AIVW run.
"""

from __future__ import annotations

import json
import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..agent.context import ArtifactLocator, BoundedContext
from .assembler_io import (
    MAX_DESIGN_IR_FIELD_BYTES,
    MAX_DESIGN_IR_RECORDS,
    bounded_object,
    locator,
    payload_artifact,
    payload_root as resolve_payload_root,
    read_json,
    records,
    sha256_file,
)
from .schema import DesignIR, DesignIRValidationError, build_design_ir
from .config_binding import ConfigBindingValidationError, bind_config_to_target


class DesignIRAssemblyError(ValueError):
    """Raised when a snapshot cannot be converted into target-bound DesignIR."""


def assemble_design_ir_from_snapshot(
    gate: Mapping[str, Any],
    payload_root: str | Path,
    *,
    behavior_evidence: Mapping[str, Any] | None = None,
    candidate_model: Mapping[str, Any] | None = None,
    config_binding: Mapping[str, Any] | None = None,
    config_binding_locator: Mapping[str, Any] | ArtifactLocator | None = None,
) -> DesignIR:
    """Load the normalized structure named by one PASS snapshot gate.

    ``gate`` must be a serialized gate result (not an executor summary).  The
    normalized structure is treated as an authenticated artifact only after a
    regular-file, payload-root, size, and SHA-256 check.
    """

    outputs = _snapshot_outputs(gate)
    root = _safe(lambda: resolve_payload_root(payload_root), "payload root")
    relative = outputs.get("normalized_structure")
    if not isinstance(relative, str) or not relative:
        raise DesignIRAssemblyError("snapshot outputs do not name normalized_structure")
    artifact = _safe(lambda: payload_artifact(root, relative), "normalized_structure")
    expected_sha256 = outputs.get("normalized_sha256")
    if not isinstance(expected_sha256, str) or len(expected_sha256) != 64 or any(
        char not in "0123456789abcdef" for char in expected_sha256
    ):
        raise DesignIRAssemblyError("snapshot normalized_structure hash is missing")
    actual_sha256 = sha256_file(artifact)
    if actual_sha256 != expected_sha256:
        raise DesignIRAssemblyError("normalized_structure hash does not match snapshot gate")
    normalized = _safe(lambda: read_json(artifact), "normalized structure")
    if normalized.get("status") != "PASS" or normalized.get("cell_count") != 1:
        raise DesignIRAssemblyError("normalized structure is not a complete PASS artifact")
    cells = normalized.get("cells")
    if not isinstance(cells, list) or len(cells) != 1 or not isinstance(cells[0], Mapping):
        raise DesignIRAssemblyError("normalized structure must contain exactly one cell")
    cell = cells[0]
    target = outputs["target"]
    view = outputs["view_identity"]
    identity = {
        "library": target["library"],
        "cell": target["cell"],
        "module": target["module"],
        "source_generation": outputs["source_generation"],
        "view": view["view"],
    }
    if cell.get("name") != target["cell"]:
        raise DesignIRAssemblyError("normalized cell identity does not match snapshot target")
    provenance = cell.get("provenance")
    normalized_identity = provenance.get("identity") if isinstance(provenance, Mapping) else None
    if not isinstance(normalized_identity, Mapping) or any(
        normalized_identity.get(key) != expected
        for key, expected in (
            ("lib", target["library"]),
            ("cell", target["cell"]),
            ("view", view["view"]),
        )
    ):
        raise DesignIRAssemblyError("normalized structure identity does not match snapshot gate")
    instances = _safe(lambda: records(cell.get("instances"), "instances"), "instances")
    nets = _safe(lambda: records(cell.get("nets"), "nets"), "nets")
    terminals = _safe(lambda: records(cell.get("terminals"), "terminals"), "terminals")
    connections = _safe(lambda: records(cell.get("connections"), "connections"), "connections")
    hierarchy = _safe(lambda: records(cell.get("hierarchy", []), "hierarchy"), "hierarchy")
    ams_binding = {
        "source": "virtuoso.snapshot",
        "signal_types": _signal_types(nets),
        "view_type": view["view_type"],
        "kind": view["kind"],
    }
    validated_config = None
    if config_binding_locator is not None and config_binding is None:
        raise DesignIRAssemblyError(
            "config binding artifact locator requires a config binding payload"
        )
    if config_binding is not None:
        try:
            validated_config = bind_config_to_target(
                config_binding,
                target=target,
                source_generation=outputs["source_generation"],
            )
        except ConfigBindingValidationError as exc:
            raise DesignIRAssemblyError(f"config binding: {exc}") from exc
        config_payload = validated_config.to_dict()
        ams_binding["config_binding"] = {
            "identity": dict(config_payload["identity"]),
            "sha256": _canonical_digest(config_payload),
            "cell_binding_count": len(config_payload["cell_bindings"]),
            "instance_binding_count": len(config_payload["instance_bindings"]),
            "discipline_binding_count": len(config_payload["discipline_bindings"]),
            "connect_rule_count": len(config_payload["connect_rules"]),
        }
        if config_binding_locator is not None:
            try:
                locator_value = (
                    config_binding_locator
                    if isinstance(config_binding_locator, ArtifactLocator)
                    else ArtifactLocator.from_dict(config_binding_locator)
                )
            except Exception as exc:
                raise DesignIRAssemblyError(
                    f"config binding artifact locator is invalid: {exc}"
                ) from exc
            if (
                locator_value.sha256 is None
                or locator_value.size is None
                or locator_value.media_type != "application/json"
            ):
                raise DesignIRAssemblyError(
                    "config binding artifact locator must include JSON hash, size, and media type"
                )
            ams_binding["config_binding"]["artifact_locator"] = locator_value.to_dict()
    evidence = {
        "snapshot_gate": {
            "executor": "virtuoso.snapshot",
            "source_generation": outputs["source_generation"],
            "target": dict(target),
            "view_identity": dict(view),
        },
        "normalized_structure": locator(artifact, root, "application/json").to_dict(),
        "normalized_sha256": actual_sha256,
        "record_counts": {
            "instances": len(instances),
            "nets": len(nets),
            "terminals": len(terminals),
            "connections": len(connections),
        },
    }
    if behavior_evidence is not None:
        evidence["behavior"] = _safe(
            lambda: bounded_object(behavior_evidence, "behavior_evidence"),
            "behavior evidence",
        )
    if validated_config is not None:
        evidence["config_binding"] = validated_config.to_dict()
    model = {} if candidate_model is None else _safe(
        lambda: bounded_object(candidate_model, "candidate_model"), "candidate model"
    )
    try:
        return build_design_ir(
            identity=identity,
            hierarchy=hierarchy,
            instances=instances,
            nets=nets,
            terminals=terminals,
            ams_binding=ams_binding,
            behavior_evidence=evidence,
            candidate_model=model,
        )
    except DesignIRValidationError as exc:
        raise DesignIRAssemblyError(str(exc)) from exc


def build_snapshot_context(
    gate: Mapping[str, Any],
    payload_root: str | Path,
    *,
    max_bytes: int = 64 * 1024,
    max_items: int = 256,
    behavior_evidence: Mapping[str, Any] | None = None,
    candidate_model: Mapping[str, Any] | None = None,
    config_binding: Mapping[str, Any] | None = None,
    config_binding_locator: Mapping[str, Any] | ArtifactLocator | None = None,
) -> BoundedContext:
    """Return a redacted, hard-capped context envelope for the agent."""

    design_ir = assemble_design_ir_from_snapshot(
        gate,
        payload_root,
        behavior_evidence=behavior_evidence,
        candidate_model=candidate_model,
        config_binding=config_binding,
        config_binding_locator=config_binding_locator,
    )
    payload = design_ir.to_dict()
    evidence = payload["behavior_evidence"]
    identity = payload["identity"]
    counts = evidence["record_counts"]
    config_summary = payload["ams_binding"].get("config_binding")
    context_locators = [evidence["normalized_structure"]]
    if isinstance(config_summary, Mapping):
        config_locator = config_summary.get("artifact_locator")
        if isinstance(config_locator, Mapping):
            context_locators.append(dict(config_locator))
    return BoundedContext(
        {
            "schema": "aivw.design_ir_context.v1",
            "design_ir_sha256": _canonical_digest(payload),
            "source_generation": identity["source_generation"],
            "target": {key: identity[key] for key in ("library", "cell", "module", "view")},
            "record_counts": dict(counts),
            "ams_binding": payload["ams_binding"],
            "config_binding": config_summary,
            "artifact_locators": context_locators,
        },
        max_bytes=max_bytes,
        max_items=max_items,
    )


def _snapshot_outputs(gate: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(gate, Mapping) or gate.get("status") != "PASS":
        raise DesignIRAssemblyError("snapshot gate must be a serialized PASS result")
    if gate.get("executor") != "virtuoso.snapshot":
        raise DesignIRAssemblyError("snapshot gate executor is not authoritative")
    outputs = gate.get("outputs")
    if not isinstance(outputs, Mapping):
        raise DesignIRAssemblyError("snapshot gate has no authoritative outputs")
    generation = outputs.get("source_generation")
    if not isinstance(generation, str) or len(generation) != 64 or any(
        char not in "0123456789abcdef" for char in generation
    ):
        raise DesignIRAssemblyError("snapshot source_generation is invalid")
    target = outputs.get("target")
    view = outputs.get("view_identity")
    if not isinstance(target, Mapping) or not isinstance(view, Mapping):
        raise DesignIRAssemblyError("snapshot target/view identity is incomplete")
    for key in ("library", "cell", "module"):
        if not isinstance(target.get(key), str) or not target[key]:
            raise DesignIRAssemblyError("snapshot target identity is incomplete")
    for key in ("library", "cell", "view", "view_type", "kind"):
        if not isinstance(view.get(key), str) or not view[key]:
            raise DesignIRAssemblyError("snapshot view identity is incomplete")
    if view["kind"] != "schematic" or view["view_type"] != "schematic":
        raise DesignIRAssemblyError("snapshot view is not a schematic")
    return {
        "source_generation": generation,
        "target": {key: target[key] for key in ("library", "cell", "module")},
        "view_identity": {key: view[key] for key in ("library", "cell", "view", "view_type", "kind")},
        "normalized_structure": outputs.get("normalized_structure"),
        "normalized_sha256": outputs.get("normalized_sha256"),
    }


def _signal_types(nets: list[dict[str, Any]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for net in nets:
        name = net.get("name")
        signal_type = net.get("signal_type")
        if isinstance(name, str) and isinstance(signal_type, str):
            result[name] = signal_type
    return dict(sorted(result.items()))


def _canonical_digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _safe(call: Any, label: str) -> Any:
    try:
        return call() if callable(call) else call
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise DesignIRAssemblyError(f"{label}: {exc}") from exc


__all__ = [
    "MAX_DESIGN_IR_FIELD_BYTES",
    "MAX_DESIGN_IR_RECORDS",
    "DesignIRAssemblyError",
    "assemble_design_ir_from_snapshot",
    "build_snapshot_context",
]
