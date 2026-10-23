"""Resolve the session capability envelope and its active task workflow.

The profile remains the immutable capability envelope.  A workflow is one
task-group view inside that envelope; it narrows the operations a task may
dispatch without changing the profile or the tool registry.  Both the bridge
and the read-only help resolve these facts here, never from a second list.
"""

from __future__ import annotations

from typing import Any, Mapping

from cadai.project_schema import OPERATIONS
from cadai.task_group_resolver import resolve
from cadai.task_group_schema import GroupDeclarationError

from ..core.contracts import RunState
from . import task_groups
from .tool_profiles import host_scope

PROFILE_EVENT = "codex.tool.profile"
WORKFLOW_EVENT = "codex.workflow"
IDLE = "idle"


def profile_id(journal) -> str:
    """The journaled session profile, or the automatic product envelope."""
    profile = None
    for row in journal.events():
        if row["kind"] == PROFILE_EVENT:
            profile = row["payload"].get("profile")
    if isinstance(profile, str) and profile:
        return profile
    from ..codex.profile_setting import AUTOMATIC_PROFILE

    return AUTOMATIC_PROFILE


def workflow_id(journal) -> str | None:
    """The last explicitly selected workflow; ``None`` means no active workflow."""
    workflow = None
    for row in journal.events():
        if row["kind"] == WORKFLOW_EVENT:
            value = row["payload"].get("workflow")
            workflow = value if isinstance(value, str) and value else None
    return workflow


def catalog(schemas) -> dict[str, Mapping[str, Any]]:
    return {row["name"]: row for row in schemas}


def scope(journal, schemas) -> dict[str, Any]:
    return host_scope(profile_id(journal), schemas)


def _view(group_id: str, schemas) -> dict[str, Any] | None:
    try:
        return resolve(
            group_id,
            declarations=task_groups.TASK_GROUPS,
            catalog=catalog(schemas),
        )
    except (KeyError, GroupDeclarationError):
        return None


def allowed_workflows(scope_row, schemas) -> list[str]:
    """Declared groups whose complete tool closure fits the envelope."""
    envelope = set(scope_row["tools"])
    result = []
    for group_id in task_groups.TASK_GROUPS:
        view = _view(group_id, schemas)
        if view is None or view["missing"] or view["conditional_missing"]:
            continue
        if set(view["tools"]) <= envelope:
            result.append(group_id)
    return sorted(result)


def operations_for(workflow: str | None, schemas) -> tuple[str, ...]:
    """Operations explicitly routed by a workflow's conditions."""
    if not workflow:
        return ()
    view = _view(workflow, schemas)
    if view is None:
        return ()
    operations = set()
    for condition in view["conditions"]:
        for route in condition["routes"]:
            operation = route.get("operation")
            if operation in OPERATIONS:
                operations.add(operation)
    return tuple(sorted(operations))


def select(journal, workflow: str | None, schemas) -> dict[str, Any]:
    """Validate a workflow request without changing session state."""
    profile = profile_id(journal)
    scope_row = host_scope(profile, schemas)
    allowed = allowed_workflows(scope_row, schemas)
    value = workflow.strip() if isinstance(workflow, str) else None
    if value in ("", IDLE):
        value = None
    if value is not None and value not in allowed:
        suggestion = next(
            (candidate for candidate in allowed
             if candidate.startswith(value) or value in candidate),
            None,
        )
        return {
            "ok": False,
            "code": "workflow_not_allowed",
            "profile": profile,
            "workflow": value,
            "allowed_workflows": allowed,
            "suggestion": suggestion,
        }
    return {
        "ok": True,
        "profile": profile,
        "workflow": value,
        "allowed_workflows": allowed,
        "allowed_operations": list(operations_for(value, schemas)),
    }


def enter(journal, workflow: str | None, schemas, context, *,
          source: str = "begin_circuit_task") -> dict[str, Any]:
    """Record one validated workflow and return its effective operations."""
    selection = select(journal, workflow, schemas)
    if not selection["ok"]:
        return selection
    previous = workflow_id(journal)
    state = journal.state or RunState(context)
    journal.append(
        WORKFLOW_EVENT,
        {
            "workflow": selection["workflow"],
            "previous": previous,
            "source": source,
        },
        state,
    )
    return {
        **selection,
        "previous": previous,
        "changed": previous != selection["workflow"],
    }


def begin_task(journal, workflow, schemas, context):
    """Validate before a native task call and return a recorder for success."""
    if journal is None or not workflow:
        return None, None
    selection = select(journal, workflow, schemas)
    if not selection["ok"]:
        return selection, None
    return selection, lambda: enter(journal, workflow, schemas, context)


def record_task(data, recorder):
    """Attach a successful workflow record to a native task result."""
    if not recorder or not isinstance(data, dict) or data.get("ok") is False:
        return data, None
    recorded = recorder()
    if not recorded["ok"]:
        return data, recorded
    data["workflow"] = {
        key: recorded.get(key)
        for key in ("workflow", "previous", "changed", "profile")
    }
    data["allowed_operations"] = recorded.get("allowed_operations", [])
    return data, None


__all__ = [
    "IDLE", "PROFILE_EVENT", "WORKFLOW_EVENT", "allowed_workflows", "begin_task",
    "catalog", "enter", "operations_for", "profile_id", "record_task", "scope",
    "select", "workflow_id",
]
