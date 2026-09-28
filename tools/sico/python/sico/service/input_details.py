"""Read an input snapshot only through its original, live history delivery."""

import json

from ..storage.input_assets import read_asset
from .published import freeze


def input_detail(stream, sequence, index=None):
    stream.validate_current()
    if type(sequence) is not int or not 1 <= sequence <= stream.committed:
        raise ValueError("Invalid input event reference")
    owner = stream.controller.journal if stream.controller is not None else stream.reader
    if stream.controller is not None:
        rows = owner.events(sequence - 1, limit=1)
        event = rows[0] if rows else None
    else:
        event = stream.events.event(sequence)
    if (event is None or event["session_id"] != stream.session_id
            or event["kind"] != "task.started"):
        raise ValueError("Input does not belong to this task history")
    payload = event["payload"]
    rows = payload.get("inputs", [])
    result = {"inputs": rows, "settings": payload.get("turn_options", {}),
              "context": payload["context"], "task_id": event["task_id"]}
    result["settings_receipt"] = {"status": "not_recorded"}
    if stream.controller is not None:
        def following():
            cursor = sequence
            while cursor < stream.committed:
                batch = owner.events(cursor, limit=min(256, stream.committed - cursor))
                if not batch:
                    return
                yield from batch
                cursor = batch[-1]["sequence"]
        records = following()
    else:
        records = (row for row in stream.iter_events() if sequence < row["sequence"])
    for record in records:
        if record["sequence"] > stream.committed or record["kind"] == "task.started":
            break
        if record["task_id"] == event["task_id"] and record["kind"] == "codex.turn.settings":
            result["settings_receipt"] = record["payload"]
            break
    if index is not None:
        if type(index) is not int or not 0 <= index < len(rows):
            raise ValueError("Invalid input index")
        row = rows[index]
        if "asset" in row:
            result["data"] = read_asset(owner, row["asset"])
            result["path"] = str(owner.root / row["asset"]["path"])
        result["input"] = row
    result["text"] = json.dumps({k: v for k, v in result.items() if k != "data"},
                                ensure_ascii=False, indent=2)
    stream.validate_current()
    return freeze(result)
