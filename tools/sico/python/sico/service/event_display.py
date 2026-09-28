"""Bounded chat projections and durable references; raw evidence stays in the journal."""

import hashlib
import json

from .published import freeze

EVENT_BYTES = 32768
BATCH_BYTES = 131072
BATCH_EVENTS = 64
TEXT_CHARS = 4096
ANSWER_CHARS = 16384
RAW_EVENTS = 512


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def bounded(value, *, chars=TEXT_CHARS, nodes=384):
    remaining, clipped = [nodes], [False]

    def visit(item, depth=0):
        remaining[0] -= 1
        if isinstance(item, str):
            if len(item) > chars:
                clipped[0] = True
                return item[:chars] + "…"
            return item
        if isinstance(item, (dict, list, tuple)):
            if remaining[0] <= 0 or depth >= 10:
                clipped[0] = True
                return {} if isinstance(item, dict) else []
            if isinstance(item, dict):
                result = {}
                for key, child in item.items():
                    if remaining[0] <= 0:
                        clipped[0] = True
                        break
                    result[key] = visit(child, depth + 1)
                return result
            result = []
            for child in item:
                if remaining[0] <= 0:
                    clipped[0] = True
                    break
                result.append(visit(child, depth + 1))
            return result
        return item

    result = visit(value)
    return result, clipped[0]


def coalesce(events):
    """Called after source/order validation; the envelope preserves the raw range."""
    statuses, skipped = {}, set()
    for index in range(len(events) - 1, -1, -1):
        event = events[index]
        key, kind = event.get("task_id"), event["kind"]
        if kind in {"model.status", "router.status"}:
            statuses.setdefault((key, kind), index)
    for index, event in enumerate(events):
        if event["kind"] != "model.delta":
            continue
        task_id = event.get("task_id")
        for following in events[index + 1:]:
            if following.get("task_id") != task_id:
                continue
            if following["kind"] in {"task.started", "tool.started"}:
                break
            if (following["kind"] == "model.completed"
                    and following["payload"].get("text")):
                skipped.add(index)
                break
    return [event for index, event in enumerate(events)
            if index not in skipped
            and statuses.get((event.get("task_id"), event["kind"]), index) == index]


class EventDisplay:
    def __init__(self):
        self.answer_chars = 0
        self.answer_clipped = False

    def project(self, raw):
        event, clipped = bounded(raw)
        kind = raw["kind"]

        # Presentation identity must survive even unusually wide tool inputs.
        def preserve_identity(value):
            for key in ("session_id", "sequence", "kind", "task_id", "timestamp"):
                if key in raw:
                    value[key] = raw[key]
            payload = value.setdefault("payload", {})
            for key in ("id", "name", "native", "status", "version"):
                if key in raw["payload"]:
                    payload[key] = raw["payload"][key]
            if kind == "tool.finished":
                original = raw["payload"]["result"]
                result = payload.setdefault("result", {})
                result["status"] = original.get("status", "")
                result["summary"] = str(original.get("summary", ""))[:1024]
            if kind.startswith("workbench.audit."):
                original = raw["payload"]
                # Typed interaction values must never silently change meaning.
                # Their full editor still uses the workbench publication.
                for key in ("elicitation", "response"):
                    if key in original:
                        if len(encoded(original[key])) > EVENT_BYTES:
                            raise ValueError("Typed audit interaction exceeds the display contract")
                        payload[key] = original[key]
                if "questions" in original:
                    payload["questions"] = [{
                        "id": q["id"], "header": q["header"], "question": q["question"][:512],
                        "options": [{"label": o["label"], "description": o["description"][:128]}
                                    for o in q["options"]],
                    } for q in original["questions"]]
                if "answers" in original:
                    payload["answers"] = {
                        key: {k: str(v)[:256] for k, v in answer.items()}
                        for key, answer in original["answers"].items()
                    }
            return value

        event = preserve_identity(event)
        clipped |= event != raw
        if kind == "task.started":
            self.answer_chars, self.answer_clipped = 0, False
        if kind == "model.delta":
            available = max(0, ANSWER_CHARS - self.answer_chars)
            text = raw["payload"]["text"][:min(TEXT_CHARS, available)]
            self.answer_chars += len(text)
            clipped = len(text) < len(raw["payload"]["text"])
            event["payload"]["text"] = text
            if not text and self.answer_clipped:
                return None
            self.answer_clipped |= clipped
        if kind == "model.completed":
            clipped |= self.answer_clipped
            self.answer_chars, self.answer_clipped = 0, False
        if kind == "tool.started":
            self.answer_chars, self.answer_clipped = 0, False
        # Keep reducing presentation content until even one very wide result
        # fits a tick. Raw payloads are validated before this projection.
        chars, nodes = TEXT_CHARS, 384
        while len(encoded(event)) > EVENT_BYTES - 512:
            chars, nodes = max(64, chars // 2), max(16, nodes // 2)
            event, _ = bounded(raw, chars=chars, nodes=nodes)
            event = preserve_identity(event)
            clipped = True
            if chars == 64 and nodes == 16:
                break
        if clipped:
            digest = hashlib.sha256(encoded(raw)).hexdigest()
            event["detail"] = "e" + str(raw["sequence"]) + "_" + digest
        size = len(encoded(event))
        if size > EVENT_BYTES:
            raise ValueError("Event presentation exceeds the bounded display contract")
        return freeze(event), size
