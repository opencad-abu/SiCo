"""PDK collection checkpoint data, directory batches and result projections."""

from __future__ import annotations

import uuid
from copy import deepcopy

from .pdk_data import binding, checked_directory
from .pdk_normalize import canonical, context, now
from .pdk_schema import MAX_CACHE_BYTES, PdkUnavailable


def ready_result(key, prepared):
    standard = prepared.get('standard', {})
    published = standard.get('design_ready', False)
    return {
        "ok": True,
        "schema_version": "cad.pdk.preparation.v1",
        "status": "ready",
        "library": key[0],
        "view": key[1],
        "source": prepared["source"],
        "path": prepared["path"],
        "directory_digest": prepared["digest"],
        "device_count": len(prepared["capture"]["data"]["items"]),
        "coverage": prepared.get("coverage"),
        "quality": prepared.get("quality"),
        "notices": prepared.get("notices", []),
        "next_action": "get_pdk_data" if published else "complete_pdk_generation",
        "design_ready": published,
        "readiness_scope": standard.get("readiness_scope", "metadata_capture_only"),
        "live_device_validation_required": True,
        "standard": prepared.get("standard", {"status": "legacy", "design_ready": False,
                                                "next_action": "prepare_pdk_data_refresh"}),
    }


def result(job):
    standard = job.get('standard', {})
    published = standard.get('design_ready', False)
    return {
        "ok": True,
        "schema_version": "cad.pdk.preparation.v1",
        **{
            k: deepcopy(v)
            for k, v in job.items()
            if k
            not in {
                "capture",
                "expected_binding",
                "devices",
                "categories",
                "excluded",
                "tasks",
                "worker_configuration",
                "standard_observations",
            }
        },
        "read_only": True,
        "design_ready": published,
        "readiness_scope": standard.get("readiness_scope", "metadata_capture_only"),
        "callbacks_executed": False,
        "next_action": (
            "advance_pdk_collection"
            if job["status"] == "collecting"
            else ("get_pdk_data" if published else "complete_pdk_generation")
            if job["status"] == "ready"
            else "respect_user_cancellation"
            if job["status"] == "cancelled"
            else "prepare_pdk_data"
            if job.get("retry_count", 0) < 2
            else "inspect_collection_failure"
        ),
    }


def cached_ready(cached, expected, raw_context, data=None, library=None):
    prepared = {
        "binding": expected,
        "capture": {
            "context": deepcopy(raw_context),
            "data": cached["payload"]["directory"],
        },
        "source": cached["source"],
        "path": cached["path"],
        "digest": cached["digest"],
        "coverage": cached["payload"]["coverage"],
        "quality": cached["payload"].get("quality"),
        "notices": cached.get("notices", []),
    }

    if data:
        from .pdk_standard.lifecycle import attach
        attach(prepared, data, library)
    return prepared


def checkpoint_matches(job, expected, raw_context, checkpoint_valid):
    try:
        if not isinstance(job.get("capture"), dict) or not isinstance(
            job["capture"].get("context"), dict
        ):
            raise ValueError("Invalid collection context")
        previous = context(job["capture"]["context"])
        current = context(raw_context)
        return (
            job.get("expected_binding") == expected
            and all(
                previous[k] == current[k]
                for k in ("session_ref", "session_generation", "project_ref")
            )
            and checkpoint_valid(job)
        )
    except (PdkUnavailable, KeyError, TypeError, ValueError):
        return False


def new_job(library, view, expected, raw_context, pooled):
    ref = "pdk_collection:" + uuid.uuid4().hex
    job = {
        "collection_ref": ref,
        "library": library,
        "view": view,
        "status": "collecting",
        "started_at": now(),
        "updated_at": now(),
        "elapsed_seconds": 0.0,
        "next_batch": 0,
        "scanned_cells": 0,
        "total_cells": None,
        "device_count": 0,
        "phase": "directory",
        "detailed_devices": 0,
        "devices": {},
        "retry_count": 0,
        "expected_binding": expected,
        "capture": {
            "context": deepcopy(raw_context),
            "data": {"items": [], "status": "complete", "truncated": False, "issues": []},
        },
        "device_scan_started": False,
        "excluded": {},
        "categories": None,
        "execution": "dbAccess_pool" if pooled else "live_session",
    }
    return job


def record_directory(job, capture):
    """Validate and append one batch to its single collection checkpoint."""
    ctx = context(capture["context"])
    original = context(job["capture"]["context"])
    if binding(ctx, job["library"]) != job["expected_binding"] or any(
        ctx[k] != original[k] for k in ("session_ref", "session_generation", "project_ref")
    ):
        raise PdkUnavailable("collection_changed", "PDK session/library changed during collection")
    records = checked_directory(capture["data"], job["library"], job["view"])
    offset, total, scanned = (records.get(k) for k in ("offset", "total_cells", "scanned_cells"))
    next_offset = records.get("next_offset")
    if offset is None and total is None and scanned is None:
        offset, scanned, total, next_offset = (
            0,
            len(records["items"]),
            len(records["items"]),
            None,
        )
    if (
        type(offset) is not int
        or offset != job["scanned_cells"]
        or type(total) is not int
        or not 0 <= total <= 20000
        or type(scanned) is not int
        or not 0 <= scanned <= 32
        or offset + scanned > total
        or scanned < len(records["items"])
        or (
            next_offset is not None
            and (
                type(next_offset) is not int
                or next_offset != offset + scanned
                or not offset < next_offset < total
            )
        )
        or (next_offset is None and offset + scanned != total)
        or (job["total_cells"] is not None and total != job["total_cells"])
    ):
        raise PdkUnavailable("collection_changed", "Directory batch coverage changed or is invalid")
    combined = {
        **job["capture"]["data"],
        "items": job["capture"]["data"]["items"] + records["items"],
    }
    checked_directory(combined, job["library"], job["view"])
    if len(canonical(combined).encode()) > MAX_CACHE_BYTES // 2:
        raise PdkUnavailable("pdk_data_limit", "Collected directory exceeds its byte budget")
    job["capture"]["data"] = combined
    job.update(
        scanned_cells=offset + scanned,
        total_cells=total,
        next_batch=job["next_batch"] + 1,
        device_count=len(combined["items"]),
    )
    if next_offset is None:
        combined.update(scanned_cells=total, total_cells=total, source=records.get("source"))
        job["phase"] = "details"
    else:
        job["status"] = "collecting"


def resume_job(job):
    """Reset unfinished tasks within the retained checkpoint retry budget."""
    for task in job.get("tasks", {}).values():
        if task["state"] != "done":
            task["state"] = "pending"
    job.update(
        status="collecting",
        retry_count=job.get("retry_count", 0) + 1,
        automatic_resume_allowed=True,
    )


def detail_tasks(job, items):
    """Initialize task tracking for a directory checkpoint, including old progress."""
    job.setdefault("excluded", {})
    if "tasks" not in job:
        job["tasks"] = {
            row["identity"]["target"]["cell"]: {
                "state": "done" if i < job["detailed_devices"] else "pending",
                "attempts": 0,
            }
            for i, row in enumerate(items)
        }


def published_ready(job, published):
    return {
        "binding": job["expected_binding"],
        "capture": job["capture"],
        "source": published["source"],
        "path": published["path"],
        "digest": published["digest"],
        "coverage": published["payload"]["coverage"],
        "quality": published["payload"].get("quality"),
        "standard": published.get("standard"),
    }
