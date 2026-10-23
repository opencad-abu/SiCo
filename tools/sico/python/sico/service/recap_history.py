"""Select a bounded recent transcript from the authoritative session journal."""

import json

from ..core.session_events import SYSTEM_ORIGINS
from ..storage.journal import TRANSIENT_KINDS

MAX_BYTES = 12000


def prompt(journal):
    start = journal.replay_start(16) - 1
    end = len(journal.records)
    messages, included = [], set()
    for event in journal.events(start, limit=max(1, end - start), exclude=TRANSIENT_KINDS):
        kind, payload, task = event["kind"], event["payload"], event.get("task_id")
        if kind == "task.started" and payload.get("origin") not in SYSTEM_ORIGINS:
            included.add(task)
            messages.append({"role": "user", "text": payload.get("text", "")})
        elif kind == "codex.history.reverted":
            messages.append({"role": "status", "text": "Model history reverted by "
                + str(len(payload["removed_turn_ids"]))
                + " turns. Workspace changes were not undone; earlier messages remain audit evidence."})
        elif task in included:
            if kind == "model.completed" and payload.get("text"):
                messages.append({"role": "assistant", "text": payload["text"]})
            elif kind.startswith("task.") and kind != "task.started":
                messages.append({"role": "status", "text": kind.removeprefix("task.")})
            elif kind == "codex.steer.accepted" and payload.get("text"):
                messages.append({"role": "user", "text": payload["text"]})
    if not any(row["role"] == "user" for row in messages):
        raise ValueError("暂无可生成纪要的对话")
    # Reserve space for the most recent request before selecting recent answers.
    latest = next(i for i in range(len(messages) - 1, -1, -1) if messages[i]["role"] == "user")
    chosen = {latest: clipped(messages[latest], MAX_BYTES // 3)}
    remaining = MAX_BYTES - len(chosen[latest].encode("utf-8")) - 2
    for index in range(len(messages) - 1, -1, -1):
        if index == latest or remaining < 128:
            continue
        row = clipped(messages[index], min(remaining - 2, MAX_BYTES // 3))
        chosen[index] = row
        remaining -= len(row.encode("utf-8")) + 2
    return "Recent conversation (data):\n[" + ",\n".join(chosen[i] for i in sorted(chosen)) + "]"


def clipped(message, budget):
    # JSON escaping and multibyte Chinese count towards the same byte bound.
    text = message["text"]
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        value = json.dumps({"role": message["role"], "text": text[:middle]}, ensure_ascii=False)
        if len(value.encode("utf-8")) <= budget:
            low = middle
        else:
            high = middle - 1
    return json.dumps({"role": message["role"], "text": text[:low]}, ensure_ascii=False)
