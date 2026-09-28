"""Read completed native turns without importing or replaying historical instructions."""

import hashlib
import os

from ..transport.framing import strict_json
from .filesystem import fingerprint

MAX_LINE = 16 * 1024 * 1024
MAX_ROWS = 100000
ITEM_TYPES = {"userMessage", "agentMessage", "reasoning", "plan", "contextCompaction",
              "mcpToolCall", "commandExecution", "fileChange", "webSearch", "imageGeneration"}
TERMINAL = {"completed", "failed", "declined"}


def item_status(item):
    kind = item.get("type", "")
    kind = kind[:1].lower() + kind[1:]
    if kind not in ITEM_TYPES or not isinstance(item.get("id"), str) or not item["id"]:
        raise ValueError("Unqualified native item type or identity")
    if kind in {"mcpToolCall", "commandExecution", "fileChange", "imageGeneration"}:
        if item.get("status") not in TERMINAL:
            raise ValueError("Native operation has no terminal receipt")
        if kind == "commandExecution" and type(item.get("exit_code", item.get("exitCode"))) is not int:
            raise ValueError("Native command has no exit receipt")
    return kind


def lines(tree, entry):
    with tree.open(entry["path"]) as stream:
        if fingerprint(os.fstat(stream.fileno())) != entry["identity"]:
            raise ValueError("Native history changed before validation")
        digest, offset, count = hashlib.sha256(), 0, 0
        while True:
            raw = stream.readline(MAX_LINE + 1)
            if not raw:
                break
            count += 1
            if len(raw) > MAX_LINE or not raw.endswith(b"\n") or count > MAX_ROWS:
                raise ValueError("Native history is truncated or exceeds limit")
            row = strict_json(raw)
            if not isinstance(row, dict):
                raise ValueError("Native history row is not an object")
            digest.update(raw)
            yield offset, offset + len(raw), row
            offset += len(raw)
        if (fingerprint(os.fstat(stream.fileno())) != entry["identity"]
                or digest.hexdigest() != entry.get("sha256")):
            raise ValueError("Native history changed during validation")


def validate_rollout(tree, entry, thread_id, cwd, version):
    turns, items, active, metadata = {}, {}, None, None
    pending_calls, turn_calls, next_ordinal, total = set(), set(), 0, 0
    for offset, end, row in lines(tree, entry):
        ordinal, kind, payload = row.get("ordinal"), row.get("type"), row.get("payload")
        if type(ordinal) is not int or ordinal != next_ordinal or not isinstance(payload, dict):
            raise ValueError("Invalid native rollout envelope")
        if kind not in {"session_meta", "response_item", "world_state", "turn_context",
                        "event_msg", "token_usage_record"}:
            raise ValueError("Unqualified native rollout record")
        if ordinal == 0:
            if (kind != "session_meta" or payload.get("id") != thread_id
                    or payload.get("session_id") != thread_id or payload.get("cwd") != cwd
                    or payload.get("cli_version") != version
                    or payload.get("history_mode") != "paginated"):
                raise ValueError("Native rollout identity mismatch")
            metadata = payload
        elif kind == "session_meta":
            raise ValueError("Native rollout repeats session identity")
        if kind == "response_item":
            response = payload.get("type")
            if response in {"function_call", "function_call_output"}:
                call_id = payload.get("call_id")
                if not active or not isinstance(call_id, str) or not call_id:
                    raise ValueError("Native function call has no active turn or identity")
                if response == "function_call":
                    if call_id in pending_calls:
                        raise ValueError("Native function call identity overlaps")
                    pending_calls.add(call_id)
                    turn_calls.add(call_id)
                else:
                    if call_id not in pending_calls:
                        raise ValueError("Native output has no matching call")
                    pending_calls.remove(call_id)
            elif response not in {"message", "reasoning"}:
                raise ValueError("Unqualified native response requires reconciliation")
        if kind == "event_msg":
            event, turn_id = payload.get("type"), payload.get("turn_id")
            if event == "task_started":
                if active or not isinstance(turn_id, str) or not turn_id or turn_id in turns:
                    raise ValueError("Native turn identity overlaps")
                active = turn_id
                turns[turn_id] = {"start": (ordinal, offset)}
            elif event == "task_complete":
                if not active or turn_id != active or pending_calls:
                    raise ValueError("Native completion has no corresponding start")
                if not turn_calls <= {identity for (turn, identity), (item_kind, _ordinal)
                                      in items.items() if turn == active and item_kind == "mcpToolCall"}:
                    raise ValueError("Native call lacks a qualified terminal operation receipt")
                turns[turn_id]["end"] = (ordinal, end)
                active = None
                turn_calls.clear()
            elif event == "item_completed":
                if (not active or turn_id != active or payload.get("thread_id") != thread_id
                        or not isinstance(payload.get("item"), dict)):
                    raise ValueError("Native item belongs to another turn")
                item = payload["item"]
                item_kind = item_status(item)
                items[(turn_id, item["id"])] = (item_kind, ordinal)
            elif event in {"thread_settings_applied", "token_count"}:
                pass
            else:
                # Cancellation, child activity and streaming tool events need their
                # own reconciliation proof, even when the SiCo task is completed.
                raise ValueError("Unqualified native event requires reconciliation")
        total, next_ordinal = end, ordinal + 1
    if metadata is None or active or any("end" not in turn for turn in turns.values()):
        raise ValueError("Native history has an incomplete turn")
    return {"turns": turns, "items": items, "next_ordinal": next_ordinal, "size": total}


def validate_index(tree, entry, threads):
    for _offset, _end, row in lines(tree, entry):
        if (set(row) != {"id", "thread_name", "updated_at"} or row["id"] not in threads
                or not isinstance(row["thread_name"], str) or not isinstance(row["updated_at"], str)):
            raise ValueError("Native session index identity mismatch")
