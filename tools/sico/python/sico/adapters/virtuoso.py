"""Bind shared context and circuit workflows to a registered Virtuoso source."""

from __future__ import annotations

from ..core.contracts import ToolResult
from ..core.tools import ReadTool, ToolRegistry, no_arguments
from ..transport.methods import QueryUnavailable
from .ade import register_ade
from .artifacts import register_artifacts
from .assistant_tools import register_assistant_tools
from .bindings import register_bindings
from .circuit import register_circuit
from .documents import register_documents
from .entry_context import register_context


def context_tools(broker, journal=None) -> ToolRegistry:
    registry = ToolRegistry()
    registry.routed = True
    registry.session_factory = lambda session: context_tools(broker, session)

    def read_context(inputs, context):
        try:
            return ToolResult(data=broker.get_context(context))
        except QueryUnavailable as exc:
            return ToolResult(exc.code, str(exc))

    registry.register(
        ReadTool(
            "get_context",
            "Read the explicitly bound Virtuoso target; never follows current focus.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            no_arguments,
            read_context,
        )
    )
    registry.register(
        ReadTool(
            "get_project_context",
            "Read the captured Virtuoso project identity independently of the entry window. "
            "source_valid=false means the captured cellview is historical, not a live view. "
            "Explicit output targets still require project preflight.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            no_arguments,
            lambda inputs, context: ToolResult(data=broker.read(context, "get_project_context")),
        )
    )
    register_ade(registry, broker)
    register_context(registry, broker)
    if journal is not None:
        register_documents(registry, journal.root.parents[2])
    if journal is not None:
        register_bindings(registry, broker, journal.session_id)
        register_artifacts(registry, journal)
        register_circuit(registry, broker, journal.root.parents[2])
        register_assistant_tools(registry, broker, journal, journal.root.parents[2])
    return registry
