"""Project-level agent mode with revision-checked atomic storage."""

from __future__ import annotations

from ..storage.roots import state_root
from ..storage import revisioned_document as document

DEFAULT = "design"
VALID = frozenset({"design", "generation"})

ModeConflictError = document.DocumentConflictError
ModeSaveUncertainError = document.DocumentSaveUncertainError


def default_path(project):
    return state_root(project) / "ai/agent-mode.json"


def _validate(value):
    if (not isinstance(value, dict) or set(value) != {"version", "pdk_workflow"}
            or value["version"] != 1 or value["pdk_workflow"] not in VALID):
        raise ValueError("智能体模式文件格式无效")
    return value["pdk_workflow"]


def read(project):
    path = default_path(project)
    value, revision = document.read(path)
    if value is None:
        workflow = DEFAULT
    else:
        try:
            workflow = _validate(value)
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError("智能体模式不可读或格式无效：" + str(exc)) from None
    return {"path": str(path), "pdk_workflow": workflow, "revision": revision,
            "editable": path.parents[1].name == ".sico"}


def save(project, workflow, *, expected_revision):
    if workflow not in VALID:
        raise ValueError("智能体模式必须是 design 或 generation")
    path = default_path(project)
    value, revision = document.save(
        project, path, {"version": 1, "pdk_workflow": workflow},
        expected_revision=expected_revision, label="智能体模式",
    )
    return {"path": str(path), "pdk_workflow": _validate(value), "revision": revision,
            "editable": path.parents[1].name == ".sico"}
