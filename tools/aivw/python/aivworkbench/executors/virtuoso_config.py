"""Virtuoso config binding orchestration owner."""

from __future__ import annotations

from typing import Mapping

from ..design_ir import bind_config_to_target, ConfigBindingValidationError
from ..executor import ExecutorContext, ExecutorResult
from ..profiles import product_root
from ..cdns_ipc import McpToolCall, CdnsIpcUnavailable
from ..workspace import sha256_file, write_json_once
from .virtuoso_support import _blocked, _call_snapshot_tools, _digest

_MAX_ITEMS = 100

def run_config_binding(context: ExecutorContext, *, call_tools=None) -> ExecutorResult:
    """Read and authenticate one config view through the existing cdns-ipc."""
    if context.metadata.get("read_only_structure_adapter") == "ldo.official_ams.v1":
        from .ldo_config_binding import run_ldo_config_binding
        return run_ldo_config_binding(context)
    snapshot = context.dependencies.get("snapshot")
    if snapshot is None or snapshot.status != "PASS":
        return _blocked(context, "BLOCKED_INPUT", "snapshot dependency did not PASS", code="snapshot_dependency")
    source_generation = snapshot.outputs.get("source_generation")
    snapshot_target = snapshot.outputs.get("target")
    if not isinstance(source_generation, str) or not _digest(source_generation):
        return _blocked(context, "BLOCKED_INPUT", "snapshot has no valid source_generation", code="missing_source_generation")
    if not isinstance(snapshot_target, Mapping):
        return _blocked(context, "BLOCKED_INPUT", "snapshot has no target identity", code="missing_target")
    views = context.recipe.target.get("source_views")
    config_view = views.get("config") if isinstance(views, Mapping) else None
    if not isinstance(config_view, str) or not config_view:
        return _blocked(context, "BLOCKED_INPUT", "recipe does not select a config view", code="config_view_unavailable")
    library = str(context.recipe.target.get("library", ""))
    cell = str(context.recipe.target.get("cell", ""))
    if snapshot_target.get("library") != library or snapshot_target.get("cell") != cell:
        return _blocked(context, "STALE_SOURCE", "config target disagrees with snapshot", code="target_mismatch")
    call = McpToolCall(
        "inspect_config_binding",
        {"lib": library, "cell": cell, "view": config_view, "max_items": _MAX_ITEMS},
    )
    entry = product_root().parent / "ai" / "python" / "sico-ai"
    try:
        result = (call_tools or _call_snapshot_tools)(
            (call,),
            environment=context.environment,
            cad_ai_entry=entry,
            timeout=context.timeout,
        )[0]
    except CdnsIpcUnavailable as exc:
        status = "BLOCKED_INPUT" if exc.code in {"config_not_found", "result_too_large"} else "BLOCKED_ENVIRONMENT"
        return _blocked(context, status, str(exc), code=exc.code)
    raw = dict(result.payload)
    if raw.get("complete") is not True or raw.get("truncated") is not False:
        return _blocked(
            context,
            "BLOCKED_INPUT",
            "config inspection did not establish a complete, untruncated binding",
            code="config_inspection_incomplete",
        )
    value = dict(raw)
    for key in ("ok", "kind", "complete", "truncated"):
        value.pop(key, None)
    value["schema_version"] = "aivw.config.binding.v1"
    value.setdefault("identity", {})
    if isinstance(value["identity"], Mapping):
        value["identity"] = dict(value["identity"])
        value["identity"]["source_generation"] = source_generation
    try:
        binding = bind_config_to_target(
            value,
            target={**snapshot_target, "config_view": config_view},
            source_generation=source_generation,
        )
    except ConfigBindingValidationError as exc:
        return _blocked(context, "BLOCKED_INPUT", str(exc), code="config_binding_invalid")
    path = context.gate_root / "config-binding.json"
    evidence_path = context.gate_root / "config-binding-evidence.json"
    write_json_once(path, binding.to_dict())
    digest = sha256_file(path)
    write_json_once(
        evidence_path,
        {
            "schema_version": 1,
            "status": "PASS",
            "provider": "authenticated-cdns-ipc-read-only",
            "source_generation": source_generation,
            "target": {key: snapshot_target[key] for key in ("library", "cell")},
            "config_identity": dict(binding.identity),
            "binding_sha256": digest,
            "complete": True,
        },
    )
    relative = path.relative_to(context.run.payload_root).as_posix()
    return ExecutorResult(
        "PASS",
        {
            "source_generation": source_generation,
            "binding_sha256": digest,
            "identity": dict(binding.identity),
            "complete": True,
        },
        {
            "binding_artifact": relative,
            "binding_sha256": digest,
            "source_generation": source_generation,
            "target": {key: snapshot_target[key] for key in ("library", "cell")},
        },
        (path, evidence_path),
    )
