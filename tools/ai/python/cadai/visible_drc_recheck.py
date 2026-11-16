"""Reuse decisions and bounded deltas for visible-DRC rechecks."""

from __future__ import annotations

import hashlib
from pathlib import Path

_MAX_CHANGED = 64


def config_identity(config_path, flow):
    try:
        digest = hashlib.sha256(Path(config_path).read_bytes()).hexdigest()
    except OSError:
        digest = ""
    identity = getattr(flow, "runset_identity", None)
    dependencies = getattr(identity, "dependencies_sha256", "")
    return {
        "config_sha256": digest,
        "runset_dependencies_sha256": dependencies or "",
    }


def _same_path(left, right):
    try:
        return Path(left).expanduser().resolve() == Path(right).expanduser().resolve()
    except (OSError, TypeError):
        return False


def reuse_basis(previous, previous_values, current):
    """Return whether a previous result can stand for the current export."""
    if previous.get("terminal") is not True or "result" not in previous:
        return False, "previous_result_unavailable"
    export = previous.get("export") or {}
    candidate = export.get("path")
    if not candidate or not Path(candidate).is_file():
        return False, "previous_artifact_unavailable"
    previous_config = previous_values.get("config_sha256") or ""
    current_config = current.get("config_sha256") or ""
    if (
        not previous_config
        or previous_config != current_config
        or previous_values.get("runset_dependencies_sha256", "")
        != current.get("runset_dependencies_sha256", "")
        or not _same_path(previous_values.get("work_root"), current.get("work_root"))
        or not _same_path(
            previous_values.get("calibre_root"), current.get("calibre_root")
        )
    ):
        return False, "config_changed"
    if list(export.get("visible_bbox") or []) != list(current.get("bbox") or []):
        return False, "window_changed"
    previous_sha = export.get("sha256")
    current_sha = (current.get("export") or {}).get("sha256")
    if not previous_sha or previous_sha != current_sha:
        return False, "geometry_changed"
    return True, "unchanged_geometry_same_config"


def delta(previous, current, basis):
    previous_checks = previous.get("checks") or {}
    current_checks = current.get("checks") or {}
    changed = []
    changed_count = 0
    new_count = 0
    resolved_count = 0
    for name in sorted(set(previous_checks) | set(current_checks)):
        before = int(previous_checks.get(name, 0) or 0)
        after = int(current_checks.get(name, 0) or 0)
        if before == after:
            continue
        changed_count += 1
        if before == 0:
            new_count += 1
        if after == 0:
            resolved_count += 1
        if len(changed) < _MAX_CHANGED:
            changed.append({
                "check": str(name)[:128],
                "before": before,
                "after": after,
                "delta": after - before,
            })
    previous_total = int(previous.get("total", 0) or 0)
    current_total = int(current.get("total", 0) or 0)
    return {
        "basis": basis,
        "previous_total": previous_total,
        "current_total": current_total,
        "total_delta": current_total - previous_total,
        "changed_count": changed_count,
        "new_check_count": new_count,
        "resolved_check_count": resolved_count,
        "changed": changed,
        "truncated": changed_count > len(changed),
    }


def discard_export(values, keep=None):
    candidate = values.get("candidate")
    if not candidate or (keep and _same_path(candidate, keep)):
        return
    for path in (Path(candidate), Path(str(candidate) + ".sico-visible.json")):
        try:
            path.unlink()
        except OSError:
            pass


__all__ = ["config_identity", "delta", "discard_export", "reuse_basis"]
