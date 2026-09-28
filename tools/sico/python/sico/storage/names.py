"""Session names are journal metadata, independent of provider labels."""

from __future__ import annotations

MAX_NAME_CHARS = 120
NAME_EVENTS = frozenset({"codex.thread", "codex.thread.name", "codex.thread.title"})


def compact_name(value, limit=MAX_NAME_CHARS):
    if not isinstance(value, str):
        return ""
    text = " ".join(value.replace("\0", "").split())
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def session_names(events):
    """Read both current metadata and older manual-name-only journals."""
    thread_id = ""
    for event in events:
        if event["kind"] == "codex.thread":
            thread_id = event["payload"].get("thread_id", "")
    result = {"name": "", "name_source": "", "title": ""}
    for event in events:
        payload = event["payload"]
        if payload.get("thread_id", thread_id) != thread_id:
            continue
        if event["kind"] == "codex.thread.name":
            name = payload.get("name")
            if name is None or isinstance(name, str):
                result["name"] = compact_name(name)
                result["name_source"] = payload.get("source") or "user"
        elif event["kind"] == "codex.thread.title" and isinstance(payload.get("title"), str):
            result["title"] = compact_name(payload["title"])
    return result
