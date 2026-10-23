"""Immutable resource-selection journal payloads and semantic default differences."""

from copy import deepcopy
import hashlib
import json
import re

from .schema import EXTENSIONS, KINDS, MAX_MANIFEST, path_value, upgrade_manifest

EVENT = "codex.resources.selected"


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def document(value):
    fields = {"path", "source", "revision", "manifest"}
    if not isinstance(value, dict) or not fields <= set(value):
        raise ValueError("资源选择记录字段缺失")
    revision = value["revision"]
    if revision is not None and (not isinstance(revision, str)
                                 or re.fullmatch("[a-f0-9]{64}", revision) is None):
        raise ValueError("资源选择修订无效")
    if value["source"] not in ("provider", "environment", "project", "legacy"):
        raise ValueError("资源选择来源无效")
    manifest = upgrade_manifest(value["manifest"])
    if revision is None and any(manifest[kind] for kind in (*KINDS, *EXTENSIONS)):
        raise ValueError("非空资源选择缺少修订证据")
    if len(json.dumps(manifest, ensure_ascii=False).encode()) > MAX_MANIFEST:
        raise ValueError("资源选择记录超过大小上限")
    return {"path": path_value(value["path"], resolve=False), "source": value["source"],
            "revision": revision, "manifest": manifest}


def create(selection, fingerprints, *, origin):
    payload = {"version": 1, "selection": document(selection),
               "fingerprints": deepcopy(fingerprints), "origin": origin}
    payload["sha256"] = digest(payload)
    return validate(payload)


def validate(value):
    if not isinstance(value, dict) or set(value) != {
        "version", "selection", "fingerprints", "origin", "sha256"
    } or type(value["version"]) is not int or value["version"] != 1:
        raise ValueError("会话资源快照无效，请新建会话")
    if value["origin"] not in ("defaults", "legacy") or not isinstance(value["fingerprints"], list):
        raise ValueError("会话资源快照来源无效")
    payload = {k: deepcopy(v) for k, v in value.items() if k != "sha256"}
    if digest(payload) != value["sha256"] or document(value["selection"]) != value["selection"]:
        raise ValueError("会话资源快照摘要不匹配，请新建会话")
    from .fingerprints import validate_fingerprints

    validate_fingerprints(value["fingerprints"], value["selection"]["manifest"])
    return deepcopy(value)


def difference(effective, defaults):
    """Report semantic changes, independent of whitespace or v1/v2 spelling."""
    old, new = upgrade_manifest(effective), upgrade_manifest(defaults)
    changes = []
    for kind in (*KINDS, *EXTENSIONS):
        field = "path" if kind == "skill_overrides" else "id"
        before = {row[field]: row for row in old[kind]}
        after = {row[field]: row for row in new[kind]}
        for key in sorted(before.keys() | after.keys()):
            if before.get(key) != after.get(key):
                action = "added" if key not in before else "removed" if key not in after else "changed"
                changes.append({"kind": kind, "id": key, "change": action})
    return changes
