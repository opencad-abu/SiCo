"""MCP adaptation for registered legacy Virtuoso workflows."""

from . import workflow_tools
from .skill_result import call_skill


def dispatch_workflow(name, arguments, *, client):
    code = workflow_tools.build_workflow_skill(
        name, arguments,
        guard_unavailable=getattr(client, "guard_legacy_workflows", False),
    )
    return call_skill(client, code, native="maestro" in name, context="workflow")
