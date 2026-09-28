"""Shared, workspace-scoped project document tools for Assistant and Copilot."""

from pathlib import Path

from cadai.document_tools import DOCUMENT_TOOLS, call_document_tool

from ..core.contracts import ToolResult
from ..core.read_dependencies import ProjectReadDependencies
from ..core.tools import Tool


def register_documents(registry, workspace):
    """Register the same document schemas and handlers used by direct MCP."""
    dependencies = ProjectReadDependencies(workspace)
    for definition in DOCUMENT_TOOLS:
        name = definition["name"]

        def validate(arguments, name=name):
            # Validation is intentionally performed by the shared handler too;
            # doing it here makes invalid arguments fail before the filesystem
            # is touched by either backend.
            from cadai.document_tools import _VALIDATORS

            _VALIDATORS[name](arguments)

        def read(arguments, context, name=name):
            cwd = context.snapshot.get("cwd")
            if not isinstance(cwd, str):
                return ToolResult("unavailable", "Missing captured project directory")
            try:
                if Path(workspace).resolve() != Path(cwd).expanduser().resolve():
                    return ToolResult(
                        "needs_reconcile",
                        "Project workspace differs from task source",
                    )
                return ToolResult(data=call_document_tool(name, arguments, workspace))
            except (ValueError, OSError) as exc:
                return ToolResult("tool_error", str(exc))

        registry.register(
            Tool(
                name,
                definition["description"],
                definition["inputSchema"],
                validate,
                read,
                execution_domain="agent",
                annotations=definition["annotations"],
                effect="read",
                input_dependencies=dependencies if name == "read_project_document" else None,
            )
        )
