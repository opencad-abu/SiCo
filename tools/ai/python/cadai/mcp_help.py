"""Read-only help over the effective MCP catalog that a host actually serves.

The catalog and the task-group declarations are injected by the caller
(defaults: cadai's canonical definitions and cadai-owned groups); the SiCo host
injects its own registered surface plus the host-owned groups so host tools and
bridge limits are described truthfully.  Availability is injected as a callable
as well; the default treats every catalogued tool as available, which is what
the cadai-managed server serves.

This module owns request parsing, budgets and dispatch only: tool views live in
``mcp_help_queries``, group views in ``mcp_help_groups``, and group closure is
resolved by ``task_group_resolver``.  Nothing here executes a tool, touches EDA
or calls a model; a truncated schema is never returned as if complete.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, NamedTuple

from . import mcp_examples
from . import mcp_help_groups
from . import mcp_help_queries as queries
from .mcp_definitions import TOOLS
from .socket_server import RequestFailure
from .task_group_schema import GroupDeclarationError, validate

MCP_HELP_ACTIONS = ("list", "describe", "schema", "workflow", "example")
MCP_HELP_ENTITIES = ("tools", "groups")

# Budgets live with the view helpers; re-exported here for the tool contract.
LIST_LIMIT = queries.LIST_LIMIT
LIST_BYTES = queries.LIST_BYTES
DESCRIBE_BYTES = queries.DESCRIBE_BYTES
SCHEMA_BYTES = queries.SCHEMA_BYTES


class Request(NamedTuple):
    action: str
    entity: str
    query: str | None
    tool: str | None
    group: str | None
    schema_path: str | None
    offset: int
    limit: int


def catalog() -> dict[str, Mapping[str, Any]]:
    """The canonical catalog in advertised order."""
    return {row["name"]: row for row in TOOLS}


def _arguments(arguments: Mapping[str, Any]) -> Request:
    if not isinstance(arguments, Mapping):
        raise RequestFailure("invalid_params", "mcp_help arguments must be an object")
    allowed = {"action", "entity", "query", "tool", "group", "schema_path", "offset", "limit"}
    unknown = set(arguments) - allowed
    if unknown:
        raise RequestFailure(
            "invalid_params", "unknown mcp_help argument: " + ", ".join(sorted(unknown)))
    action = arguments.get("action", "list")
    if action not in MCP_HELP_ACTIONS:
        raise RequestFailure("invalid_params",
                             "action must be list, describe, schema, workflow or example")
    entity = arguments.get("entity", "tools")
    if entity not in MCP_HELP_ENTITIES:
        raise RequestFailure("invalid_params", "entity must be tools or groups")
    query, tool_name = arguments.get("query"), arguments.get("tool")
    for key, value, maximum in (("query", query, 256), ("tool", tool_name, 96)):
        if value is not None and (not isinstance(value, str) or not 0 < len(value) <= maximum):
            raise RequestFailure("invalid_params", f"invalid {key}")
    group_name = arguments.get("group")
    if group_name is not None and (not isinstance(group_name, str)
                                   or not 0 < len(group_name) <= 96):
        raise RequestFailure("invalid_params", "invalid group")
    schema_path = arguments.get("schema_path")
    if schema_path is not None and (not isinstance(schema_path, str) or len(schema_path) > 512):
        raise RequestFailure("invalid_params", "invalid schema_path")
    if action == "describe" and (tool_name is None) == (group_name is None):
        raise RequestFailure("invalid_params", "describe takes exactly one of tool or group")
    if action == "schema" and (tool_name is None or group_name is not None):
        raise RequestFailure("invalid_params", "schema requires tool and no group")
    if action == "workflow" and group_name is None:
        raise RequestFailure("invalid_params", "workflow requires group")
    if action == "example" and (tool_name is None) == (group_name is None):
        raise RequestFailure("invalid_params", "example takes exactly one of tool or group")
    if action == "list" and tool_name is not None:
        raise RequestFailure("invalid_params", "list does not take tool")
    if action == "list" and entity == "groups" and group_name is not None:
        raise RequestFailure("invalid_params", "group search does not take group")
    offset = arguments.get("offset", 0)
    limit = arguments.get("limit", LIST_LIMIT)
    if type(offset) is not int or offset < 0:
        raise RequestFailure("invalid_params", "offset must be a nonnegative integer")
    if type(limit) is not int or limit < 1:
        raise RequestFailure("invalid_params", "limit must be a positive integer")
    return Request(action, entity, query, tool_name, group_name, schema_path, offset,
                   min(limit, LIST_LIMIT))


def _groups(groups: Mapping[str, Mapping[str, Any]] | None):
    from .task_groups import GROUPS

    declarations = GROUPS if groups is None else groups
    try:
        return validate(declarations)
    except GroupDeclarationError as exc:
        raise RequestFailure("group_declarations_invalid", str(exc)) from exc


def _help_note(name: str) -> str | None:
    """On-demand detail declared next to the contract; newest source wins."""
    from .mcp_definitions import TOOLS_BY_NAME

    note = TOOLS_BY_NAME.get(name, {}).get("help")
    return note if isinstance(note, str) and note.strip() else None


def mcp_help(arguments: Mapping[str, Any], workspace: Any = None, *,
             catalog_rows: Mapping[str, Mapping[str, Any]] | None = None,
             availability=None,
             groups: Mapping[str, Mapping[str, Any]] | None = None,
             session: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Answer one bounded help request from the injected catalog and groups."""
    del workspace  # accepted for resource-handler symmetry; help reads no files

    request = _arguments(arguments)
    rows_by_name = dict(catalog_rows) if catalog_rows is not None else catalog()
    if request.action == "list" and request.entity == "groups":
        return mcp_help_groups.group_search(
            _groups(groups), rows_by_name, availability, request.query, request.limit)
    if request.action == "workflow":
        return mcp_help_groups.workflow_view(
            _groups(groups), rows_by_name, availability, request.group)
    if request.action == "example":
        if request.tool is not None:
            if request.tool not in rows_by_name:
                raise RequestFailure("unknown_tool",
                                     f"no such tool: {request.tool}；可用 list entity=tools 检索")
            view = mcp_examples.tool_view(request.tool)
            result: dict[str, Any] = {"ok": True, "action": "example", "complete": True,
                                      "truncated": False}
            if view is None:
                result.update(tool=request.tool, example=None,
                              hint="该工具暂无受控样例；schema 仍是权威模式，"
                                   "也可在所属组内用 example 检索其他成员。")
            else:
                result.update(view)
            return queries.bounded(result, mcp_examples.BUDGET_BYTES, None)
        return mcp_help_groups.example_listing(
            _groups(groups), rows_by_name, availability, request.group)
    if request.action == "describe" and request.group is not None:
        return mcp_help_groups.group_view(
            _groups(groups), rows_by_name, availability, request.group, request.offset)
    if request.action == "list" and request.group is not None:
        return mcp_help_groups.scoped_tools(
            _groups(groups), rows_by_name, availability, request.group, request.query,
            request.offset, request.limit)
    if request.action == "list":
        return queries.list_tools(rows_by_name, request.query, request.offset,
                                  request.limit, availability)
    definition, legacy = queries.canonical(rows_by_name, request.tool)
    name = definition["name"]
    if request.action == "describe":
        return queries.describe_tool(
            definition, legacy, name,
            queries.availability(name, availability), rows_by_name,
            help_note=_help_note(name), session=session)
    return queries.schema_view(definition, legacy, name, request.schema_path,
                               rows_by_name, SCHEMA_BYTES)


__all__ = ["MCP_HELP_ACTIONS", "MCP_HELP_ENTITIES", "catalog", "mcp_help"]
