"""Write a read-only context shadow without changing the model request."""

from __future__ import annotations

from .contracts import canonical_bytes
from .envelope import build_envelope
from .manifest import build_manifest
from .payload import build_payload

RENDERER_VERSION = "sico.context.renderer.v1"


def _artifact(journal, payload):
    return journal.artifact(canonical_bytes(payload))


def record_entry_context_shadow(journal, context_record, entry, *, turn_step,
                                renderer_version=RENDERER_VERSION):
    """Record payload/manifest events for one captured entry context.

    ``journal`` is a TaskJournal: it must expose ``artifact`` and ``emit``.
    No payload content is sent to the model or copied into the journal event.
    """
    if not isinstance(context_record, dict) or not isinstance(entry, dict):
        raise ValueError("context shadow inputs must be objects")
    snapshot = context_record.get("snapshot")
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    instance = context_record.get("instance_id") or snapshot.get("instance_id") or "unknown"
    generation = context_record.get("generation") or snapshot.get("generation") or "unknown"
    snapshot_id = f"{instance}:{generation}"
    task = getattr(getattr(journal, "state", None), "task", None)
    task = task if isinstance(task, dict) else {}
    turn_id = task.get("id") or "context-shadow"
    session_owner = getattr(journal, "journal", None)
    session_id = getattr(session_owner, "session_id", "") or snapshot.get("session_id") or "unknown"
    project_id = snapshot.get("project_id") or ""
    work = snapshot.get("work") if isinstance(snapshot.get("work"), dict) else None
    stage = snapshot.get("stage") if isinstance(snapshot.get("stage"), dict) else None
    payload = build_payload(
        entry, context_record, turn_step=turn_step, snapshot_id=snapshot_id,
        work=work, stage=stage,
    )
    artifact = _artifact(journal, payload)
    sections = payload["completeness"]["sections"]
    truncated = sorted(
        name for name, value in sections.items()
        if value.get("state") not in ("complete", "summary")
    )
    layers = [{
        "kind": "session",
        "id": "entry_context",
        "scope": "turn",
        "authority": "source",
        "source_ref": "cad.entry.context.v1",
        "revision": entry.get("session_ref") or "",
        "sha256": artifact["sha256"],
        "content_ref": artifact["path"],
        "included": True,
        "reason": "captured entry context",
        "truncated": bool(truncated),
    }]
    envelope = build_envelope(
        payload,
        turn_id=turn_id,
        session_id=session_id,
        project_id=project_id,
        layers=layers,
        budget={
            "bytes": artifact["size"],
            "tokens": None,
            "tool_calls": 1,
            "truncated": truncated,
        },
        created_at=payload.get("captured_at"),
    )
    manifest = build_manifest(
        payload, envelope, artifact, renderer_version=renderer_version,
        work_id=str((work or {}).get("work_id") or ""),
        stage_id=str((stage or {}).get("stage_id") or ""),
    )
    journal.emit("context.payload.snapshot", {
        "snapshot_id": snapshot_id,
        "turn_step": turn_step,
        "payload_ref": artifact,
        "payload_sha256": artifact["sha256"],
    })
    journal.emit("context.manifest", manifest)
    return manifest
