"""Bounded Codex child-task projection for the execution detail tree."""

MAX_STORED_CHILDREN = 40


def record_child(executions, task, payload, timestamp):
    execution = executions.get(task)
    thread_id = payload.get("thread_id")
    if execution is None or not isinstance(thread_id, str) or not thread_id:
        return
    children = execution.setdefault("children", {})
    row = children.get(thread_id, {})
    row.update({
        "thread_id": thread_id[:96],
        "parent_thread_id": str(payload.get("parent_thread_id", ""))[:96],
        "name": str(payload.get("name", "Codex 子任务"))[:200],
        "status": str(payload.get("status", "started"))[:32],
        "tool": str(payload.get("tool", ""))[:64],
        "updated_at": timestamp,
    })
    children[thread_id] = row
    if len(children) > MAX_STORED_CHILDREN:
        keep = sorted(children.values(), key=lambda value: value.get("updated_at", ""),
                      reverse=True)[:MAX_STORED_CHILDREN]
        execution["children"] = {value["thread_id"]: value for value in keep}
