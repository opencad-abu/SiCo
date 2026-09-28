"""Synchronize public Codex thread metadata without reading its private database."""

from __future__ import annotations

from ..storage.names import MAX_NAME_CHARS, compact_name, session_names
from .rpc import RpcError


class ThreadNames:
    def __init__(self, backend, records):
        self.backend = backend
        self.metadata = session_names(records)
        self.setting = None

    def _name(self, value, source="codex.name"):
        if value is not None and not isinstance(value, str):
            return
        name = compact_name(value)
        if name == self.metadata["name"]:
            if source == "codex.name" or source == self.metadata["name_source"]:
                return
        self.backend._event("codex.thread.name", {
            "thread_id": self.backend.thread_id, "name": name, "source": source,
        })
        self.metadata.update(name=name, name_source=source)

    def notification(self, event):
        if event.get("method") != "thread/name/updated" or "id" in event:
            return False
        params = event.get("params")
        if (self.backend.thread_id and isinstance(params, dict)
                and params.get("threadId") == self.backend.thread_id):
            value = params.get("threadName")
            source = "codex.name"
            if self.setting and value == self.setting[0]:
                source = self.setting[1]
            self._name(value, source)
        return True

    def capture(self, thread):
        if not isinstance(thread, dict) or thread.get("id") != self.backend.thread_id:
            return
        self.metadata.update(self.backend.journal.name_metadata)
        # A resume response may report ``name: null`` while the durable local
        # journal still contains a manual name. Only a concrete native name
        # replaces that value; an explicit clear arrives through the
        # thread/name/updated notification and is handled there.
        if isinstance(thread.get("name"), str) and compact_name(thread["name"]):
            self._name(thread["name"])
        # In 0.154 Thread has name/preview, not the internal SQLite title field.
        if isinstance(thread.get("preview"), str):
            title = compact_name(thread["preview"])
            if title != self.metadata["title"]:
                self.backend._event("codex.thread.title", {
                    "thread_id": self.backend.thread_id, "title": title,
                    "source": "codex.preview",
                })
                self.metadata["title"] = title

    @staticmethod
    def validate(name):
        if not isinstance(name, str) or not name.strip() or "\0" in name:
            raise ValueError("会话名称不能为空或包含空字符")
        if len(name.strip()) > MAX_NAME_CHARS:
            raise ValueError("会话名称过长")
        return compact_name(name)

    def set(self, rpc, name, *, source="user", timeout=30):
        value = self.validate(name)
        self.setting = (value, source)
        try:
            result = rpc.request("thread/name/set", {
                "threadId": self.backend.thread_id, "name": value,
            }, timeout=timeout)
            self._name(value, source)
            return result
        finally:
            self.setting = None

    def after_turn(self, rpc):
        """Obtain Codex's preview, then name an unnamed conversation once."""
        try:
            result = rpc.request("thread/read", {
                "threadId": self.backend.thread_id, "includeTurns": False,
            }, timeout=2)
            if (not isinstance(result, dict) or not isinstance(result.get("thread"), dict)
                    or result["thread"].get("id") != self.backend.thread_id):
                raise ValueError("Invalid thread metadata")
            self.capture(result["thread"])
            if not self.metadata["name"] and self.metadata["title"]:
                self.set(rpc, compact_name(self.metadata["title"], 60),
                         source="codex.preview", timeout=2)
        except (OSError, RuntimeError, RpcError, ValueError, TypeError):
            # Metadata failure must not turn a completed CAD task into a retry.
            self.backend._event("codex.thread.name_sync_failed", {
                "thread_id": self.backend.thread_id,
                "message": "Thread name synchronization failed; no task was replayed",
            })
