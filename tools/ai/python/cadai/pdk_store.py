"""Bounded session-local frozen snapshots and query-bound opaque pagination."""
from __future__ import annotations

import json
import secrets
import uuid
from collections import OrderedDict
from copy import deepcopy

from .pdk_normalize import canonical, digest
from .pdk_schema import MAX_CACHE_BYTES, MAX_RESPONSE_BYTES, MAX_SNAPSHOTS, PdkUnavailable

MAX_RETAINED_DEVICES = 128
MAX_RETAINED_BYTES = 32 * 1024 * 1024


class PdkStore:
    def __init__(self):
        self.snapshots = OrderedDict()
        self.cursors = OrderedDict()
        self.retained = {}

    def add(self, value):
        ref = "snapshot:" + uuid.uuid4().hex
        value["context"]["snapshot_ref"] = ref
        self.snapshots[ref] = value
        self.trim(ref)
        return ref

    def trim(self, active):
        while (len(self.snapshots) > MAX_SNAPSHOTS
               or sum(len(canonical(s).encode()) for s in self.snapshots.values()) > MAX_CACHE_BYTES):
            oldest = next(iter(self.snapshots))
            if len(self.snapshots) == 1:
                del self.snapshots[oldest]
                raise PdkUnavailable("cache_limit", "Snapshot exceeds the bounded cache")
            if oldest == active:
                self.snapshots.move_to_end(oldest)
                continue
            del self.snapshots[oldest]

    def get(self, ref):
        if ref not in self.snapshots:
            raise PdkUnavailable("snapshot_unavailable", "Snapshot evicted or belongs to another MCP process")
        return self.snapshots[ref]

    def retain_device(self, ref, device_ref):
        """Retain only a bound device's validation baseline, never its search results."""
        snap = self.get(ref)
        old = snap["details"].get(device_ref)
        if old is None:
            raise PdkUnavailable("device_unavailable", "Device detail required before retention")
        baseline = {key: old[key] for key in ("target", "revision", "dependency_digests")}
        retained = self.retained.get(ref, {
            "context": snap["context"], "details": {},
            "device_index": {key: (snap.get("device_index") or {}).get(key)
                             for key in ("digest", "source", "relocation")},
            "cache_policy": "retained_for_bound_adapter",
        })
        candidate = {**self.retained, ref: {
            **retained, "details": {**retained["details"], device_ref: baseline},
        }}
        if (sum(len(s["details"]) for s in candidate.values()) > MAX_RETAINED_DEVICES
                or len(canonical(candidate).encode()) > MAX_RETAINED_BYTES):
            raise PdkUnavailable("cache_limit", "Bound device retained baseline capacity reached")
        self.retained = candidate

    def validation_snapshot(self, ref):
        if ref in self.snapshots:
            return self.snapshots[ref]
        if ref in self.retained:
            return self.retained[ref]
        return self.get(ref)

    def offset(self, ref, query, cursor):
        if cursor is None:
            return 0
        value = self.cursors.get(cursor)
        if value is None or value[:2] != (ref, digest(query)):
            raise PdkUnavailable("cursor_mismatch", "Cursor must match the snapshot, query, sections and page size")
        return value[2]

    def page(self, response, items, ref, query, offset, limit, *, assign):
        """Bound the entire pretty-printed MCP payload; never skip an oversize item."""
        selected = []
        response = deepcopy(response)
        for item in items[offset:offset + limit]:
            selected.append(deepcopy(item))
            assign(response, selected)
            # 4 KiB reserves the page metadata, cursor and JSONL escaping overhead.
            if len(json.dumps(response, ensure_ascii=False, indent=2).encode()) > MAX_RESPONSE_BYTES - 4096:
                selected.pop()
                break
        if not selected and offset < len(items):
            raise PdkUnavailable("item_too_large", "Single item exceeds response limit; select fewer detail sections")
        assign(response, selected)
        end = offset + len(selected)
        cursor = None
        if end < len(items):
            cursor = "cursor:" + secrets.token_urlsafe(24)
            self.cursors[cursor] = (ref, digest(query), end)
            while len(self.cursors) > 2048:
                self.cursors.popitem(last=False)
        response["page"] = {"offset": offset, "returned": len(selected), "total": len(items),
                            "next_cursor": cursor, "truncated": end < len(items),
                            "response_limit_bytes": MAX_RESPONSE_BYTES}
        if len(json.dumps(response, ensure_ascii=False, indent=2).encode()) > MAX_RESPONSE_BYTES:
            raise PdkUnavailable("metadata_too_large", "Selected metadata exceeds the response limit")
        return response
