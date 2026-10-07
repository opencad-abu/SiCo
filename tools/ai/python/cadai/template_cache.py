"""Bounded pure-data caches for template content, search and plan artifacts.

The cache is an acceleration layer only.  Keys include the complete immutable
dependencies supplied by the caller, values are JSON data copied on both
boundaries, and a miss/fallback never changes the authoritative verifier.  No
Cadence handle, preparation receipt, write permission or live session object is
accepted because cache values must be finite canonical JSON.
"""

from __future__ import annotations

import copy
import math
import os
import threading
import time
from collections import OrderedDict

from .template_schema import TemplateError, canonical, digest

CACHE_SCHEMA = "cad.template.cache.v1"
READER_SCHEMA_VERSION = "template-record-reader.v1"
CONTENT_CACHE_BYTES = 64 * 1024 * 1024
SEARCH_CACHE_BYTES = 16 * 1024 * 1024
PLAN_CACHE_BYTES = 32 * 1024 * 1024
MAX_ENTRY_BYTES = 4 * 1024 * 1024
NO_MATCH_TTL_SECONDS = 30.0
EVENTS = ("disabled", "hit", "miss", "evicted", "fallback")
LAYERS = ("content", "search", "plan")


def _copy_json(value):
    """Return a defensive copy after proving the value is bounded JSON."""

    # Canonicalization rejects handles, NaN and unsupported objects before a
    # value can enter or leave the cache.  The copy keeps nested lists/maps
    # independent from both the producer and the next caller.
    canonical(value).encode("utf-8")
    return copy.deepcopy(value)


class ByteLRUCache:
    """A small thread-safe LRU bounded by serialized bytes, not item count."""

    def __init__(self, max_bytes, *, max_entry_bytes=MAX_ENTRY_BYTES,
                 enabled=True, clock=time.monotonic):
        if type(max_bytes) is not int or max_bytes < 0:
            raise TemplateError("cache byte budget must be a non-negative integer")
        if type(max_entry_bytes) is not int or max_entry_bytes <= 0:
            raise TemplateError("cache entry budget must be a positive integer")
        self.max_bytes = max_bytes
        self.max_entry_bytes = max_entry_bytes
        self.enabled = bool(enabled) and max_bytes > 0
        self._clock = clock
        self._items = OrderedDict()
        self._bytes = 0
        self._lock = threading.RLock()
        self._last_event = "disabled" if not self.enabled else "miss"
        self._hits = self._misses = self._evictions = self._skipped = 0

    @property
    def bytes_used(self):
        with self._lock:
            return self._bytes

    @property
    def last_event(self):
        with self._lock:
            return self._last_event

    @property
    def stats(self):
        with self._lock:
            return {
                "entries": len(self._items),
                "bytes": self._bytes,
                "max_bytes": self.max_bytes,
                "max_entry_bytes": self.max_entry_bytes,
                "hits": self._hits,
                "misses": self._misses,
                "evictions": self._evictions,
                "skipped": self._skipped,
            }

    def _event(self, value):
        self._last_event = value
        return value

    def clear(self):
        with self._lock:
            self._items.clear()
            self._bytes = 0
            self._event("evicted" if self.enabled else "disabled")

    def get(self, key):
        if not isinstance(key, str) or not key:
            raise TemplateError("cache key must be non-empty text")
        with self._lock:
            if not self.enabled:
                return None
            row = self._items.get(key)
            if row is None:
                self._misses += 1
                self._event("miss")
                return None
            value, expires_at, size = row
            if expires_at is not None and self._clock() >= expires_at:
                self._items.pop(key, None)
                self._bytes -= size
                self._evictions += 1
                self._event("evicted")
                return None
            self._items.move_to_end(key)
            self._hits += 1
            self._event("hit")
            try:
                return _copy_json(value)
            except (TypeError, ValueError, RecursionError, UnicodeError, TemplateError):
                # A value should have been validated at put time.  Treat any
                # unexpected mutation/corruption as a safe cache fallback.
                self._items.pop(key, None)
                self._bytes -= size
                self._evictions += 1
                self._event("fallback")
                return None

    def put(self, key, value, *, ttl=None):
        if not isinstance(key, str) or not key:
            raise TemplateError("cache key must be non-empty text")
        if ttl is not None and (
            type(ttl) not in (int, float) or not math.isfinite(float(ttl)) or ttl <= 0
        ):
            raise TemplateError("cache TTL must be positive")
        with self._lock:
            if not self.enabled:
                return self._event("disabled")
            try:
                stored = _copy_json(value)
                size = len(canonical(stored).encode("utf-8"))
            except (TypeError, ValueError, RecursionError, UnicodeError, TemplateError):
                self._skipped += 1
                return self._event("fallback")
            if size > self.max_entry_bytes or size > self.max_bytes:
                self._skipped += 1
                return self._event("fallback")
            old = self._items.pop(key, None)
            if old is not None:
                self._bytes -= old[2]
            expires_at = None if ttl is None else self._clock() + float(ttl)
            self._items[key] = (stored, expires_at, size)
            self._bytes += size
            evicted = False
            while self._bytes > self.max_bytes and self._items:
                _, (_, _, removed_size) = self._items.popitem(last=False)
                self._bytes -= removed_size
                self._evictions += 1
                evicted = True
            # A put follows a miss in the read path.  Keep the public event
            # vocabulary about the lookup outcome; a subsequent get is the
            # operation that reports ``hit``.
            return self._event("evicted" if evicted else "miss")


def _key(kind, dependencies):
    if kind not in LAYERS:
        raise TemplateError("unsupported cache layer: " + str(kind))
    try:
        return kind + ":" + digest({"schema": CACHE_SCHEMA, "kind": kind,
                                    "dependencies": dependencies})
    except (TypeError, ValueError, RecursionError, UnicodeError) as exc:
        raise TemplateError("cache key dependencies are not canonical JSON") from exc


def content_cache_key(template_ref, record_digest, reader_schema_version):
    for name, value in (("template_ref", template_ref), ("record_digest", record_digest),
                        ("reader_schema_version", reader_schema_version)):
        if not isinstance(value, str) or not value:
            raise TemplateError(name + " is required for a content cache key")
    return _key("content", {"template_ref": template_ref, "record_digest": record_digest,
                             "reader_schema_version": reader_schema_version})


def search_cache_key(dependencies):
    """Key a complete search by every dependency supplied by the caller."""

    return _key("search", dependencies)


def plan_cache_key(dependencies):
    """Key pure ready plan data against all current preparation dependencies."""

    return _key("plan", dependencies)


class TemplateCache:
    """Three independent byte-bounded LRU layers with a small negative TTL."""

    def __init__(self, *, enabled=True, content_bytes=CONTENT_CACHE_BYTES,
                 search_bytes=SEARCH_CACHE_BYTES, plan_bytes=PLAN_CACHE_BYTES,
                 max_entry_bytes=MAX_ENTRY_BYTES, no_match_ttl=NO_MATCH_TTL_SECONDS,
                 clock=time.monotonic):
        if (
            type(no_match_ttl) not in (int, float)
            or not math.isfinite(float(no_match_ttl))
            or no_match_ttl <= 0
        ):
            raise TemplateError("no_match cache TTL must be positive")
        self.enabled = bool(enabled)
        self.no_match_ttl = float(no_match_ttl)
        self.content = ByteLRUCache(content_bytes, max_entry_bytes=max_entry_bytes,
                                    enabled=enabled, clock=clock)
        self.search = ByteLRUCache(search_bytes, max_entry_bytes=max_entry_bytes,
                                   enabled=enabled, clock=clock)
        self.plan = ByteLRUCache(plan_bytes, max_entry_bytes=max_entry_bytes,
                                 enabled=enabled, clock=clock)
        self._layers = {"content": self.content, "search": self.search, "plan": self.plan}
        self._content_keys = OrderedDict()
        self._content_key_limit = 10000

    def _layer(self, name):
        try:
            return self._layers[name]
        except KeyError as exc:
            raise TemplateError("unsupported cache layer: " + str(name)) from exc

    def get(self, layer, key):
        return self._layer(layer).get(key)

    def put(self, layer, key, value, *, negative=False):
        ttl = self.no_match_ttl if layer == "search" and negative else None
        return self._layer(layer).put(key, value, ttl=ttl)

    def event(self, layer):
        return self._layer(layer).last_event

    def stats(self):
        return {name: cache.stats for name, cache in self._layers.items()}

    def content_get(self, snapshot_ref, template_ref, member_digest=None,
                    reader_schema_version=READER_SCHEMA_VERSION):
        """Read content through a snapshot-scoped lookup to the full key.

        The lookup is only an in-memory accelerator.  The actual cache key still
        contains the template reference, record digest and reader schema.
        ``snapshot_ref`` binds the lookup to the catalog bytes already
        validated by the current request.
        """

        if not isinstance(snapshot_ref, str) or not snapshot_ref:
            raise TemplateError("snapshot_ref is required for content cache lookup")
        if not isinstance(template_ref, str) or not template_ref:
            raise TemplateError("template_ref is required for content cache lookup")
        if not isinstance(member_digest, str) or not member_digest:
            raise TemplateError("member_digest is required for content cache lookup")
        if not isinstance(reader_schema_version, str) or not reader_schema_version:
            raise TemplateError("reader_schema_version is required for content cache lookup")
        lookup = (snapshot_ref, member_digest, template_ref, reader_schema_version)
        with self.content._lock:
            key = self._content_keys.get(lookup)
            if key is None:
                if self.content.enabled:
                    self.content._misses += 1
                    self.content._event("miss")
                return None
            self._content_keys.move_to_end(lookup)
        value = self.content.get(key)
        if value is None:
            with self.content._lock:
                self._content_keys.pop(lookup, None)
        return value

    def clear(self, layer=None):
        """Drop one layer or all layers and remove its content lookup aliases."""

        names = LAYERS if layer is None else (layer,)
        for name in names:
            self._layer(name).clear()
        if layer is None or layer == "content":
            with self.content._lock:
                self._content_keys.clear()

    def content_put(self, snapshot_ref, template_ref, record, record_digest,
                    member_digest=None,
                    reader_schema_version=READER_SCHEMA_VERSION):
        if not isinstance(snapshot_ref, str) or not snapshot_ref:
            raise TemplateError("snapshot_ref is required for content cache insertion")
        if not isinstance(template_ref, str) or not template_ref:
            raise TemplateError("template_ref is required for content cache insertion")
        if not isinstance(member_digest, str) or not member_digest:
            raise TemplateError("member_digest is required for content cache insertion")
        key = content_cache_key(template_ref, record_digest, reader_schema_version)
        outcome = self.content.put(key, record)
        if outcome not in {"fallback", "disabled"}:
            with self.content._lock:
                lookup = (snapshot_ref, member_digest, template_ref, reader_schema_version)
                self._content_keys[lookup] = key
                self._content_keys.move_to_end(lookup)
                while len(self._content_keys) > self._content_key_limit:
                    self._content_keys.popitem(last=False)
        return outcome


_CACHE_REGISTRY = {}
_CACHE_REGISTRY_LOCK = threading.RLock()


def cache_enabled_from_environment(environment=None):
    environment = os.environ if environment is None else environment
    value = str(environment.get("SICO_TEMPLATE_CACHE", "on")).strip().casefold()
    if value in {"", "1", "true", "yes", "on", "enabled"}:
        return True
    if value in {"0", "false", "no", "off", "disabled"}:
        return False
    raise TemplateError("SICO_TEMPLATE_CACHE must be on or off")


def cache_for_root(root, *, enabled=None):
    """Return the process-local cache for one private reader root."""

    key = str(root)
    if enabled is None:
        enabled = cache_enabled_from_environment()
    with _CACHE_REGISTRY_LOCK:
        cache = _CACHE_REGISTRY.get(key)
        if cache is None or cache.enabled != bool(enabled):
            cache = TemplateCache(enabled=enabled)
            _CACHE_REGISTRY[key] = cache
        return cache


__all__ = [
    "CACHE_SCHEMA",
    "READER_SCHEMA_VERSION",
    "CONTENT_CACHE_BYTES",
    "SEARCH_CACHE_BYTES",
    "PLAN_CACHE_BYTES",
    "MAX_ENTRY_BYTES",
    "NO_MATCH_TTL_SECONDS",
    "EVENTS",
    "LAYERS",
    "ByteLRUCache",
    "TemplateCache",
    "content_cache_key",
    "search_cache_key",
    "plan_cache_key",
    "cache_enabled_from_environment",
    "cache_for_root",
]
