"""Rebuildable necessary-condition indexes for a fixed catalog snapshot.

The index is only a candidate ordering/filter hint.  It stores no mapping or
approval decision and is never authoritative: callers must reload the record
from the same snapshot and run exact/core validation.  A corrupt or mismatched
index is therefore safe to discard and report as ``index_fallback``.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import Counter
from pathlib import Path

from .template_graph import MATCHER_VERSION as MATCHER_VERSION
from .template_schema import TemplateError, canonical, digest

INDEX_SCHEMA = "cad.template.index.v1"
INDEX_RULE_VERSION = "necessary-counts.v1"
NORMALIZER_VERSION = "template-canonical.v1"
MAX_INDEX_ROWS = 10000
MAX_INDEX_BYTES = 16 * 1024 * 1024


def _row(record, origin):
    topology = record.get("topology") or {}
    devices = topology.get("devices") or []
    kinds = Counter(device.get("kind") for device in devices)
    reuse = record.get("reuse_contract") or {}
    core_ids = reuse.get("core_devices")
    if core_ids is None:
        required = devices
    else:
        if not isinstance(core_ids, list) or len(set(core_ids)) != len(core_ids):
            raise TemplateError("index reuse_contract.core_devices must be unique")
        by_id = {device.get("id"): device for device in devices}
        if set(core_ids) - set(by_id):
            raise TemplateError("index core_devices refers to an absent device")
        required = [by_id[device_id] for device_id in core_ids]
    required_kinds = Counter(device.get("kind") for device in required)
    return {
        "template_ref": record["template_ref"],
        "record_digest": digest({key: value for key, value in record.items() if key != "summary"}),
        "device_count": len(devices),
        "device_kinds": dict(sorted((kind, count) for kind, count in kinds.items() if kind)),
        "required_device_count": len(required),
        "required_device_kinds": dict(
            sorted((kind, count) for kind, count in required_kinds.items() if kind)
        ),
        "port_count": len(topology.get("ports") or []),
        "net_count": len(topology.get("nets") or []),
        "fingerprint": topology.get("fingerprint"),
        "origin": origin,
    }


def _write(path, value):
    raw = (canonical(value) + "\n").encode("utf-8")
    if len(raw) > MAX_INDEX_BYTES:
        raise TemplateError("template index exceeds byte budget")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".index-", dir=path.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def build_index(snapshot, records, output):
    """Build an index from records already read from one fixed snapshot."""

    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("snapshot_ref"), str):
        raise TemplateError("index requires a fixed snapshot description")
    if not isinstance(records, (list, tuple)) or len(records) > MAX_INDEX_ROWS:
        raise TemplateError("index candidate count exceeds limit")
    rows = []
    seen = set()
    for record in records:
        if not isinstance(record, dict) or record.get("template_ref") in seen:
            raise TemplateError("index records must have unique template references")
        seen.add(record["template_ref"])
        rows.append(_row(record, record.get("origin", "unknown")))
    rows.sort(key=lambda row: row["template_ref"])
    payload = {
        "schema": INDEX_SCHEMA,
        "snapshot_ref": snapshot["snapshot_ref"],
        "schema_versions": sorted({record.get("schema_version") for record in records}),
        "normalizer_version": NORMALIZER_VERSION,
        "matcher_version": snapshot.get("matcher_version", MATCHER_VERSION),
        "index_rule_version": INDEX_RULE_VERSION,
        "rows": rows,
    }
    payload["index_digest"] = hashlib.sha256(canonical(payload).encode()).hexdigest()
    _write(output, payload)
    return payload


def _validate_row(row):
    if not isinstance(row, dict):
        raise TemplateError("index row must be an object")
    required = {
        "template_ref", "record_digest", "device_count", "device_kinds",
        "required_device_count", "required_device_kinds", "port_count", "net_count",
        "fingerprint", "origin",
    }
    if set(row) != required:
        raise TemplateError("index row fields are incomplete")
    if not all(isinstance(row[key], str) for key in ("template_ref", "record_digest", "origin")):
        raise TemplateError("index row identity fields are invalid")
    for key in ("device_count", "required_device_count", "port_count", "net_count"):
        if type(row[key]) is not int or not 0 <= row[key] <= 10000:
            raise TemplateError("index row count is invalid")
    if row["required_device_count"] > row["device_count"]:
        raise TemplateError("index required device count exceeds total device count")
    for key in ("device_kinds", "required_device_kinds"):
        values = row[key]
        if not isinstance(values, dict) or len(values) > 64:
            raise TemplateError("index device kinds are invalid")
        if any(
            not isinstance(kind, str) or type(count) is not int or not 0 <= count <= 10000
            for kind, count in values.items()
        ):
            raise TemplateError("index device kind count is invalid")
    if any(
        count > row["device_kinds"].get(kind, 0)
        for kind, count in row["required_device_kinds"].items()
    ):
        raise TemplateError("index required device kinds exceed total device kinds")
    return row


def load_index(path, snapshot_ref, *, matcher_version=None,
               normalizer_version=None, index_rule_version=None):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_INDEX_BYTES:
        return None, "index_fallback"
    try:
        value = json.loads(path.read_bytes(), parse_constant=lambda _: (_ for _ in ()).throw(
            ValueError("non-finite index number")
        ))
        if not isinstance(value, dict) or value.get("schema") != INDEX_SCHEMA \
                or value.get("snapshot_ref") != snapshot_ref:
            return None, "index_fallback"
        expected_versions = {
            "normalizer_version": normalizer_version,
            "matcher_version": matcher_version,
            "index_rule_version": index_rule_version,
        }
        if any(expected is not None and value.get(key) != expected
               for key, expected in expected_versions.items()):
            return None, "index_fallback"
        if any(not isinstance(value.get(key), str) for key in expected_versions):
            return None, "index_fallback"
        digest_value = value.get("index_digest")
        unsigned = {key: item for key, item in value.items() if key != "index_digest"}
        if digest_value != hashlib.sha256(canonical(unsigned).encode()).hexdigest():
            return None, "index_fallback"
        rows = value.get("rows")
        if not isinstance(rows, list) or len(rows) > MAX_INDEX_ROWS:
            return None, "index_fallback"
        seen = set()
        for row in rows:
            _validate_row(row)
            if row["template_ref"] in seen:
                return None, "index_fallback"
            seen.add(row["template_ref"])
        return value, None
    except (OSError, TypeError, ValueError, KeyError, RecursionError, TemplateError):
        return None, "index_fallback"


def validate_index_records(index, records):
    """Check every current record against its indexed necessary facts.

    Extra rows are allowed because an index may be built before policy filtering
    or match-mode selection.  Missing or changed rows invalidate the index: a
    partial index must never silently create a false negative.
    """

    rows = {row["template_ref"]: row for row in index["rows"]}
    for record in records:
        reference = record.get("template_ref") if isinstance(record, dict) else None
        if reference not in rows:
            return False
        try:
            expected = _row(record, rows[reference].get("origin", "unknown"))
        except (KeyError, TypeError, TemplateError):
            return False
        actual = rows[reference]
        for key in (
            "template_ref", "record_digest", "device_count", "device_kinds",
            "required_device_count", "required_device_kinds", "port_count", "net_count",
            "fingerprint",
        ):
            if actual.get(key) != expected.get(key):
                return False
    return True


def necessary_candidates(index, *, device_kinds=None, available_device_kinds=None,
                         min_devices=None, max_devices=None,
                         count_field="device_count", kind_field="device_kinds"):
    """Apply only proven necessary count conditions, preserving all rows.

    ``device_kinds`` expresses a lower bound (exact search uses it together
    with equal total counts); ``available_device_kinds`` expresses an upper
    bound for required core devices.  Keeping the two directions explicit
    prevents a core search from accidentally treating optional peripherals as
    required template content.
    """

    if index is None:
        return []
    required = Counter(device_kinds or {})
    available = Counter(available_device_kinds or {})
    rows = []
    for row in index["rows"]:
        if count_field not in row or kind_field not in row:
            raise TemplateError("index is missing the requested count fields")
        if min_devices is not None and row[count_field] < min_devices:
            continue
        if max_devices is not None and row[count_field] > max_devices:
            continue
        kinds = row[kind_field]
        if any(kinds.get(kind, 0) < count for kind, count in required.items()):
            continue
        if available and any(
            count > available.get(kind, 0) for kind, count in kinds.items()
        ):
            continue
        rows.append(row["template_ref"])
    return rows


__all__ = [
    "INDEX_SCHEMA",
    "INDEX_RULE_VERSION",
    "MATCHER_VERSION",
    "NORMALIZER_VERSION",
    "build_index",
    "load_index",
    "validate_index_records",
    "necessary_candidates",
]
