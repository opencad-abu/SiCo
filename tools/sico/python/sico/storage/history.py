"""Incremental session catalogs with compatibility exports for the journal reader."""

from __future__ import annotations

import json
import os
from pathlib import Path

from ..core.contracts import identifier
from ..transport.framing import strict_json
from .history_reader import (
    SessionReader as SessionReader,
)

# Compatibility imports retain the historical module entry; migrate imports to history_reader.
from .history_reader import (
    TailWindow as TailWindow,
)
from .history_reader import (
    owned_directory as owned_directory,
)
from .history_reader import (
    validate_event as validate_event,
)
from .index import (
    METADATA_KINDS,
)
from .journal import SessionJournal, open_private, private_dir
from .names import session_names

CACHE_CONTRACT = "cadai.catalog.cache.v2"
CACHE_DIRECTORY = "cache"
CACHE_NAME = "catalog.json"









def catalog_row(session_id, modified, events):
    thread_rows = [e["payload"] for e in events if e["kind"] == "codex.thread"]
    root_thread_id = thread_rows[-1].get("thread_id", "") if thread_rows else ""
    children_by_id = {}
    for event in events:
        if event["kind"] != "codex.child":
            continue
        child = event["payload"]
        child_id = child.get("thread_id")
        if not isinstance(child_id, str) or not child_id:
            continue
        current = children_by_id.setdefault(child_id, {"thread_id": child_id})
        current.update({
            key: value for key, value in child.items()
            if key in {"parent_thread_id", "name", "status", "tool"}
            and value not in (None, "")
        })
    by_parent = {}
    for child in children_by_id.values():
        parent = child.get("parent_thread_id") or root_thread_id
        by_parent.setdefault(parent, []).append(child)

    def nest(parent, seen=None):
        seen = set() if seen is None else seen
        result = []
        for child in by_parent.get(parent, []):
            child_id = child["thread_id"]
            if child_id in seen:
                continue
            item = dict(child)
            item["children"] = nest(child_id, seen | {child_id})
            result.append(item)
        return result

    row = {"id": session_id, "modified": modified, **session_names(events),
           "thread_id": root_thread_id, "children": nest(root_thread_id)}
    if any(e["kind"] == "codex.thread.deleted" for e in events):
        row["unavailable"] = "已删除"
    return row


class CatalogIndex:
    """Cache validated offsets and metadata, never whole transcripts or model state."""

    METADATA = METADATA_KINDS

    def __init__(self, root):
        self.root = Path(root)
        self._entries = {}
        self._cache_loaded = False
        self._dirty = False

    # One metadata row per session plus its fingerprint; a fresh desktop
    # process reuses it instead of re-reading a gigabyte of journals.
    def _cache_path(self):
        return self.root / CACHE_DIRECTORY / CACHE_NAME

    def read_session(self, session_id):
        """Read one current catalog row, rechecking components even on a cache hit."""
        self._load_cache()
        row = self._read(identifier(session_id))
        if self._dirty:
            self._save_cache()
            self._dirty = False
        return row

    def _load_cache(self):
        if self._cache_loaded:
            return
        self._cache_loaded = True
        try:
            fd = open_private(self._cache_path(), os.O_RDONLY)
        except (OSError, ValueError):
            return
        try:
            with os.fdopen(fd, "rb") as stream:
                data = strict_json(stream.read())
        except (OSError, ValueError, TypeError):
            return
        if not isinstance(data, dict) or data.get("contract") != CACHE_CONTRACT:
            return
        sessions = data.get("sessions")
        if not isinstance(sessions, dict):
            return
        for key, value in sessions.items():
            if isinstance(key, str) and isinstance(value, dict):
                self._entries[key] = value

    def _save_cache(self):
        try:
            directory = self.root / CACHE_DIRECTORY
            private_dir(directory)
            payload = json.dumps(
                {"contract": CACHE_CONTRACT, "sessions": self._entries},
                ensure_ascii=False, allow_nan=False, separators=(",", ":"),
            ).encode("utf-8")
            temporary = directory / (CACHE_NAME + ".tmp")
            fd = open_private(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY)
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
            os.replace(temporary, directory / CACHE_NAME)
        except (OSError, ValueError, TypeError, KeyError):
            return

    @staticmethod
    def _fingerprint_of(info):
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    def _read(self, session_id):
        """Read one journal, retrying a raced resume with a full re-read.

        A cached offset can become invalid after an in-place rewrite. Retry
        once from the start; a genuinely unreadable journal still raises.
        Replacement files and shrinking files already start at offset zero.
        """
        cached = self._entries.get(session_id)
        if cached is not None:
            try:
                return self._read_once(session_id, cached)
            except (ValueError, OSError, KeyError, TypeError):
                self._entries.pop(session_id, None)
                self._dirty = True
        return self._read_once(session_id, None)

    def _read_once(self, session_id, cached):
        reader = SessionReader(self.root, session_id)
        fd = open_private(reader.directory / "events.jsonl", os.O_RDONLY)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            fingerprint = self._fingerprint_of(info)
            if cached is not None and tuple(cached["fingerprint"]) == fingerprint:
                self._components(reader, cached)
                return cached["row"]
            offset, sequence, metadata, managed = 0, 0, [], False
            previous = tuple(cached["fingerprint"]) if cached is not None else None
            if (previous is not None and previous[:2] == fingerprint[:2]
                    and previous[2] < info.st_size):
                offset, sequence = cached["offset"], cached["sequence"]
                metadata = list(cached["metadata"])
                managed = cached.get("managed", False)
            from .record_integrity import MANAGED_EVENTS

            stream.seek(offset)
            # The scan resumes from the last offset and keeps one metadata row
            # per session, so listing stays cheap however long the journal is.
            for raw_row in reader.iter_raw(stream, sequence=sequence, validate=False):
                sequence = raw_row["sequence"]
                managed |= raw_row["kind"] in MANAGED_EVENTS
                if raw_row["kind"] in self.METADATA:
                    metadata.append(SessionJournal.public(raw_row))
            row = catalog_row(session_id, info.st_mtime, metadata)
            entry = {
                "fingerprint": fingerprint, "offset": stream.tell(),
                "sequence": sequence, "metadata": metadata, "row": row, "managed": managed,
            }
            self._components(reader, entry)
            self._entries[session_id] = entry
            self._dirty = True
            return row

    @staticmethod
    def _components(reader, entry):
        from .record_integrity import require_components
        from .session_snapshot import read

        if entry["row"].get("unavailable") == "已删除":
            return
        threads = [e["payload"] for e in entry["metadata"] if e["kind"] == "codex.thread"]
        require_components(reader.root, reader.session_id, managed=entry.get("managed", False),
                           empty=not entry["sequence"],
                           thread=threads[-1] if threads and not reader.migration else None)
        read(reader.directory)

    def scan(self, progress=None):
        """Scan every session; ``progress(rows)`` may publish partial results.

        Sessions are visited newest first so a first-ever (cacheless) scan can
        show the most recent conversations while the rest is still parsed.
        """

        owned_directory(self.root)
        owned_directory(self.root / "sessions")
        self._load_cache()
        rows = {}
        from .record_removal import DIRECTORY, removed

        sessions = {path.name: path for path in (self.root / "sessions").iterdir()}
        for path in (self.root / DIRECTORY).glob("*.json"):
            sessions.setdefault(path.stem, self.root / "sessions" / path.stem)
        def modified(path):
            try:
                return path.lstat().st_mtime
            except FileNotFoundError:
                return 0
        sessions = sorted(sessions.values(), key=modified, reverse=True)
        for path in sessions:
            try:
                identifier(path.name)
            except ValueError:
                continue
            try:
                if removed(self.root, path.name):
                    rows[path.name] = {"id": path.name, "unavailable": "已删除"}
                    continue
                rows[path.name] = self._read(path.name)
            except (ValueError, OSError, KeyError, TypeError) as exc:
                self._entries.pop(path.name, None)
                self._dirty = True
                rows[path.name] = {
                    "id": path.name,
                    "unavailable": "记录不可用",
                    "unavailable_detail": f"{type(exc).__name__}: {exc}",
                    "modified": modified(path),
                }
                from .record_removal import revision

                try:
                    revision(self.root, path.name)
                    rows[path.name].update(damaged=True, unavailable="内容不完整，仅可删除")
                except (OSError, ValueError):
                    pass
            if progress is not None:
                progress(rows)
        kept = {key: value for key, value in self._entries.items() if key in rows}
        if len(kept) != len(self._entries):
            self._dirty = True
        self._entries = kept
        if self._dirty:
            self._save_cache()
            self._dirty = False
        return rows


def session_catalog(root):
    root = Path(root)
    owned_directory(root)
    owned_directory(root / "sessions")
    rows = [row for row in CatalogIndex(root).scan().values() if not row.get("unavailable")]
    return sorted(rows, key=lambda r: r["modified"], reverse=True)[:200]
