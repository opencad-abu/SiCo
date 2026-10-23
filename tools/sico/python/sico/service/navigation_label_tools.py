"""Narrow SiCo tools for creating labels and classifying the current session."""

from __future__ import annotations

from contextlib import nullcontext

from ..core.contracts import ToolResult
from ..core.tools import Tool


def _create_arguments(args):
    if not isinstance(args, dict) or set(args) != {"name"}:
        raise ValueError("create_session_label accepts one name")


def _set_arguments(args):
    if not isinstance(args, dict) or set(args) != {"label_id"}:
        raise ValueError("set_current_session_label accepts one label_id")
    if not isinstance(args["label_id"], str) or len(args["label_id"]) > 96:
        raise ValueError("label_id is invalid")


def _empty_arguments(args):
    if args:
        raise ValueError("This tool accepts no arguments")


def attach_label_tools(loop, labels):
    """Attach tools that share the service's sole label writer."""

    def snapshot():
        value = labels.snapshot()
        if value.get("error"):
            raise ValueError(value["error"])
        return value

    def list_labels(_args, _context):
        return ToolResult(data=snapshot(), summary="会话标签布局")

    def create_label(args, _context):
        state = snapshot()
        value = labels.create(args["name"], expected_revision=state["revision"])
        return ToolResult(data=value, summary="会话标签已创建")

    def set_current_label(args, _context):
        state = snapshot()
        label_id = args["label_id"].strip() or None
        value = labels.move_sessions(
            [loop.journal.session_id], label_id, None,
            expected_revision=state["revision"],
        )
        return ToolResult(data=value, summary="当前会话标签已更新")

    definitions = (
        (
            "list_session_labels",
            "List project session labels and their stable label IDs. Use this before "
            "classifying a session. The operation does not start a task or change a session.",
            list_labels,
            {},
            [],
            {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
            "read",
        ),
        (
            "create_session_label",
            "Create one project session label, or return the existing label with the same "
            "case-insensitive name. This does not assign the current session.",
            create_label,
            {"name": {"type": "string", "minLength": 1, "maxLength": 40}},
            ["name"],
            {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
            "unknown",
        ),
        (
            "set_current_session_label",
            "Set the current session's single project label. Use an empty label_id to "
            "remove the session from its label. This changes navigation metadata only.",
            set_current_label,
            {"label_id": {"type": "string", "maxLength": 96}},
            ["label_id"],
            {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
            "unknown",
        ),
    )
    for name, description, handler, properties, required, tool_annotations, effect in definitions:
        schema = {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        }

        def invoke(args, context, handler=handler):
            try:
                with getattr(loop, "lock", nullcontext()):
                    return handler(args, context)
            except (OSError, ValueError, TypeError) as exc:
                return ToolResult("tool_error", str(exc))

        validate = _create_arguments if name == "create_session_label" else (
            _set_arguments if name == "set_current_session_label" else
            _empty_arguments
        )
        loop.tools.register(Tool(
            name, description, schema, validate, invoke,
            execution_domain="local",
            annotations=tool_annotations,
            effect=effect,
        ))
