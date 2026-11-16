"""Build the bounded per-turn context manifest."""

from __future__ import annotations

from .contracts import MANIFEST_SCHEMA, sha256_json, string


def build_manifest(payload, envelope, payload_ref, *, renderer_version,
                   work_id="", stage_id="", stage_revision=None):
    if not isinstance(payload, dict) or not isinstance(envelope, dict):
        raise ValueError("payload and envelope must be objects")
    if not isinstance(payload_ref, dict):
        raise ValueError("payload_ref must be an object")
    digest = payload_ref.get("sha256")
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValueError("payload_ref sha256 is invalid")
    string(renderer_version, "renderer_version", allow_empty=False)
    for value, name in ((work_id, "work_id"), (stage_id, "stage_id")):
        string(value, name)
    if stage_revision is not None and type(stage_revision) is not int:
        raise ValueError("stage_revision must be an integer or null")
    layers = []
    for layer in envelope.get("layers", []):
        if not isinstance(layer, dict):
            raise ValueError("envelope layer must be an object")
        layers.append({
            key: layer.get(key)
            for key in ("kind", "id", "scope", "authority", "revision", "truncated")
        })
    target = payload.get("active_target")
    target = target if isinstance(target, dict) else {}
    return {
        "schema_version": MANIFEST_SCHEMA,
        "envelope_id": envelope.get("envelope_id"),
        "envelope_sha256": sha256_json(envelope),
        "payload_ref": dict(payload_ref),
        "payload_sha256": digest,
        "turn_step": payload.get("turn_step"),
        "work_id": work_id,
        "stage_id": stage_id,
        "stage_revision": stage_revision,
        "renderer_version": renderer_version,
        "target_generation": target.get("generation"),
        "layer_summary": layers,
        "selection": envelope.get("selection", {}),
        "budget": envelope.get("budget", {}),
        "excluded": envelope.get("excluded", []),
    }
