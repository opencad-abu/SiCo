"""Versioned immutable session snapshots generated only on the event worker."""

from ..core.contracts import BoundContext
from .event_display import bounded, encoded
from .published import freeze

STATE_BYTES = 524288
MAX_PUBLISHED_CHILDREN = 2000


def _bounded_tree(tree):
    if not isinstance(tree, dict):
        return {"works": [], "unassigned": [], "truncated": True}
    works = []
    child_budget = MAX_PUBLISHED_CHILDREN
    children_truncated = False
    for work in list(tree.get("works") or ())[:64]:
        if not isinstance(work, dict):
            continue
        stages = []
        for stage in list(work.get("stages") or ())[:128]:
            if not isinstance(stage, dict):
                continue
            executions = []
            for row in list(stage.get("executions") or ())[:200]:
                if not isinstance(row, dict):
                    continue
                raw_children = [child for child in list(row.get("children") or ())[:20]
                                if isinstance(child, dict)]
                children = []
                for child in raw_children:
                    if child_budget <= 0:
                        children_truncated = True
                        break
                    children.append({
                        "thread_id": str(child.get("thread_id", ""))[:96],
                        "parent_thread_id": str(child.get("parent_thread_id", ""))[:96],
                        "name": str(child.get("name", ""))[:200],
                        "status": str(child.get("status", ""))[:32],
                        "tool": str(child.get("tool", ""))[:64],
                        "updated_at": str(child.get("updated_at", ""))[:64],
                    })
                    child_budget -= 1
                if len(raw_children) > len(children):
                    children_truncated = True
                executions.append({
                    "id": str(row.get("id", ""))[:96],
                    "task_id": str(row.get("task_id", ""))[:96],
                    "text": str(row.get("text", ""))[:512],
                    "status": str(row.get("status", ""))[:32],
                    "origin": str(row.get("origin", ""))[:32],
                    "diagnostic": str(row.get("diagnostic", ""))[:512],
                    "inherited": bool(row.get("inherited")),
                    "children": children,
                })
            stages.append({
                "id": str(stage.get("id", ""))[:96],
                "title": str(stage.get("title", ""))[:200],
                "objective": str(stage.get("objective", ""))[:2000],
                "acceptance": str(stage.get("acceptance", ""))[:4000],
                "status": str(stage.get("status", ""))[:32],
                "status_source": str(stage.get("status_source", ""))[:32],
                "reason": str(stage.get("reason", ""))[:2000],
                "blocker": str(stage.get("blocker", ""))[:2000],
                "next_step": str(stage.get("next_step", ""))[:2000],
                "updated_at": str(stage.get("updated_at", ""))[:64],
                "plan_revision": stage.get("plan_revision"),
                "order": stage.get("order", 0),
                "depends_on": [
                    str(value)[:96] for value in stage.get("depends_on") or ()
                    if isinstance(value, str)
                ][:32],
                "plan_note": str(stage.get("plan_note", ""))[:2000],
                "facts": {
                    key: bool((stage.get("facts") or {}).get(key))
                    for key in ("active", "blocked", "reported")
                },
                "conflict": bool(stage.get("conflict")),
                "executions": executions,
            })
        works.append({
            "id": str(work.get("id", ""))[:96],
            "title": str(work.get("title", ""))[:200],
            "status": str(work.get("status", ""))[:32],
            "stages": stages,
        })
    unassigned = []
    for row in list(tree.get("unassigned") or ())[:200]:
        if not isinstance(row, dict):
            continue
        unassigned.append({
            "id": str(row.get("id", ""))[:96],
            "task_id": str(row.get("task_id", ""))[:96],
            "text": str(row.get("text", ""))[:512],
            "status": str(row.get("status", ""))[:32],
            "origin": str(row.get("origin", ""))[:32],
            "diagnostic": str(row.get("diagnostic", ""))[:512],
        })
    return {"works": works, "unassigned": unassigned,
            "truncated": bool(tree.get("truncated")),
            "children_truncated": children_truncated}


def state_view(snapshot):
    shown = dict(snapshot)
    binding = snapshot.get("binding_state") or {}
    if (len(snapshot["requests"]) > 200
            or any(len(binding.get(key, [])) > 64 for key in
                   ("targets", "reservations", "operations", "proposals", "holds", "recent"))
            or len((snapshot.get("router") or {}).get("requests", [])) > 64):
        raise ValueError("Session control inventory exceeds the display limit")
    task = snapshot["task"]
    keys = ("id", "status", "diagnostic", "activity", "waiting_audits",
            "supplementary_audits", "input_tokens",
            "output_tokens", "total_tokens", "context_usage", "steer_turn", "steer_blocked")
    shown["task"], _ = bounded({key: task[key] for key in keys if key in task}, chars=1024)
    for key in ("id", "waiting_audits", "supplementary_audits"):
        if key in task:
            shown["task"][key] = task[key]
    shown["requests"] = []
    for row in snapshot["requests"]:
        item = dict(row)
        item["text"] = str(item.get("text", ""))[:512]
        item["diagnostic"] = str(item.get("diagnostic", ""))[:512]
        shown["requests"].append(item)
    if "task_tree" in snapshot:
        shown["task_tree"] = _bounded_tree(snapshot["task_tree"])
    contexts = (BoundContext.from_record(snapshot["context"]),
                BoundContext.from_record(snapshot["default_context"]))
    for context in contexts:
        object.__setattr__(context, "snapshot", freeze(context.snapshot))
    # Control identities are exact. Never truncate a target or silently omit
    # controls when an abnormal snapshot exceeds the supported display size.
    if len(encoded(shown)) > STATE_BYTES:
        raise ValueError("Session control snapshot exceeds the display limit")
    return freeze(shown), contexts


class StatePublication:
    def __init__(self, controller):
        self.controller = controller
        self._key = None
        self._published = None

    def key(self):
        owner = self.controller
        context = owner.current
        return (owner.version, context.instance_id, context.generation,
                owner.closing, owner._cancel.is_set(), owner.paused)

    def changed(self):
        return self.key() != self._key

    def read(self, committed):
        owner, key = self.controller, self.key()
        if key != self._key:
            from .frontend_session import publish_session

            publish_session(owner)
            self._published = state_view(owner.updates._state_snapshot(committed))
            self._key = key
        state, contexts = self._published
        # The versioned state may stay unchanged while journal-only evidence
        # advances. Updating this envelope shares the immutable nested payload.
        return freeze({**state, "sequence": committed}), contexts
