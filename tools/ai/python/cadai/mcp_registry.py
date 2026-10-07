"""Canonical MCP tool registry construction.

The individual tool modules own their schemas.  This module owns the one
operation that turns those schemas into the server's ordered public registry
and name index.  Keeping that operation here prevents callers from rebuilding
the registry with subtly different duplicate or replacement rules.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .agent_profile import SUPPORTED_PROFILES, allowed_tool_names


class RegistryError(ValueError):
    """Raised when a tool registry cannot be constructed safely."""


def build_registry(
    *tool_groups: Iterable[Mapping[str, Any]],
    replace_names: Iterable[str] = (),
    replacements: Iterable[Mapping[str, Any]] = (),
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Build the ordered MCP tool list and its canonical name index.

    ``tool_groups`` preserve the server's existing order.  ``replacements``
    are appended after removing ``replace_names``; this is used for the entry
    context tools, whose definitions supersede legacy aliases.  Both returned
    collections reference the same definition objects so schema consumers and
    dispatch adapters cannot observe divergent copies.
    """
    removed = frozenset(replace_names)
    tools: list[dict[str, Any]] = []
    for group in tool_groups:
        for definition in group:
            if not isinstance(definition, Mapping):
                raise RegistryError("MCP tool definitions must be mappings")
            name = definition.get("name")
            if not isinstance(name, str) or not name:
                raise RegistryError("MCP tool definitions require a non-empty name")
            if name not in removed:
                tools.append(dict(definition))

    for definition in replacements:
        if not isinstance(definition, Mapping):
            raise RegistryError("MCP replacement definitions must be mappings")
        name = definition.get("name")
        if not isinstance(name, str) or not name:
            raise RegistryError("MCP replacement definitions require a non-empty name")
        tools.append(dict(definition))

    by_name: dict[str, dict[str, Any]] = {}
    for definition in tools:
        name = definition["name"]
        if name in by_name:
            raise RegistryError(f"duplicate MCP tool name: {name}")
        by_name[name] = definition
    return tools, by_name


def require_registered_names(
    by_name: Mapping[str, Mapping[str, Any]],
    *name_groups: Iterable[str],
) -> None:
    """Fail during startup when a handler advertises an unregistered tool."""
    missing = sorted(
        {name for group in name_groups for name in group if name not in by_name}
    )
    if missing:
        raise RegistryError("handler tools missing from MCP registry: " + ", ".join(missing))


def require_bidirectional_bindings(
    by_name: Mapping[str, Mapping[str, Any]],
    executor_names: Iterable[str],
) -> None:
    """Require every registered schema to have one declared execution binding.

    The registry is the schema authority; a dispatch table may be assembled
    from domain sets, but it must cover exactly the same public names.  This
    catches both a schema that has no route and a stale route for a removed
    tool during startup.
    """
    schemas = set(by_name)
    executors = set(executor_names)
    missing = sorted(schemas - executors)
    stale = sorted(executors - schemas)
    if missing or stale:
        detail = []
        if missing:
            detail.append("missing executor: " + ", ".join(missing))
        if stale:
            detail.append("stale executor: " + ", ".join(stale))
        raise RegistryError("MCP schema/executor bindings disagree; " + "; ".join(detail))


class ToolArgumentError(ValueError):
    """An executor's declared invalid-argument failure."""


@dataclass(frozen=True)
class ToolDefinition:
    """One schema (including annotations), executor and capability policy."""

    schema: dict[str, Any]
    executor: Callable
    profiles: frozenset[str]
    argument_errors: tuple[type[Exception], ...] = ()
    compact: bool = False

    @property
    def name(self) -> str:
        return self.schema["name"]

    def allows(self, profile: str) -> bool:
        return profile in self.profiles

    def invoke(self, arguments: dict[str, Any], *, profile: str):
        if not self.allows(profile):
            raise ToolArgumentError("unknown tool or invalid arguments")
        try:
            return self.executor(self.name, arguments)
        except self.argument_errors as exc:
            raise ToolArgumentError(str(exc)) from exc


def bind_tools(by_name, groups) -> dict[str, ToolDefinition]:
    """Bind explicit domain groups and fail on missing, stale or duplicate routes.

    Each group supplies names, a callable, its argument errors and compact-output
    policy. Preserve schema order, even if the groups use unordered name sets.
    Profile policy is resolved from agent_profile, never inferred from read-only
    annotations (some verification tools deliberately write bounded artifacts).
    """
    bindings = {}
    for names, executor, errors, compact in groups:
        if not callable(executor):
            raise RegistryError("MCP bindings require callable executors")
        for name in names:
            if name in bindings:
                raise RegistryError(f"duplicate MCP executor: {name}")
            bindings[name] = (executor, errors, compact)
    require_bidirectional_bindings(by_name, bindings)
    policies = {
        profile: frozenset(allowed_tool_names(profile, by_name))
        for profile in SUPPORTED_PROFILES
    }
    return {
        name: ToolDefinition(
            schema, bindings[name][0],
            frozenset(profile for profile, names in policies.items() if name in names),
            bindings[name][1], bindings[name][2],
        )
        for name, schema in by_name.items()
    }


__all__ = [
    "RegistryError",
    "build_registry",
    "require_bidirectional_bindings",
    "ToolDefinition",
    "ToolArgumentError",
    "bind_tools",
    "require_registered_names",
]
