"""MCP handler for local search, documents, skill checks, and references."""

from __future__ import annotations

from typing import Any

from .document_tools import DOCUMENT_NAMES, DocumentError, call_document_tool
from .search import search
from .skill_check_tools import check_skill
from .skill_reference import (
    SKILL_REFERENCE_TOOL_NAMES,
    SkillReference,
    SkillReferenceArgumentError,
    SkillReferenceUnavailable,
)
from .socket_server import RequestFailure
from .tool_help import tool_help


RESOURCE_NAMES = frozenset({"Search", "tool_help", "check_skill"} | DOCUMENT_NAMES | SKILL_REFERENCE_TOOL_NAMES)


class ResourceHandlerArgumentError(ValueError):
    """Raised when a resource tool has invalid arguments."""


def dispatch_resource(
    name: str,
    arguments: dict[str, Any],
    *,
    workspace: Any,
    skill_reference: SkillReference,
) -> tuple[bool, dict[str, Any]]:
    """Dispatch one bounded resource tool without owning server resources."""
    if name not in RESOURCE_NAMES:
        raise ValueError(f"unknown resource tool: {name}")
    if name in {"Search", "tool_help"}:
        try:
            detail = (search if name == "Search" else tool_help)(arguments, workspace)
        except RequestFailure as exc:
            if exc.code == "invalid_params":
                raise ResourceHandlerArgumentError(str(exc)) from exc
            return False, exc.as_error()
        return detail["ok"], detail
    if name == "check_skill":
        try:
            detail = check_skill(arguments, workspace)
        except RequestFailure as exc:
            if exc.code == "invalid_params":
                raise ResourceHandlerArgumentError(str(exc)) from exc
            detail = exc.as_error()
            detail.update({"phase": "preflight", "executed": False})
            return False, detail
        return detail["ok"], detail
    if name in DOCUMENT_NAMES:
        try:
            detail = call_document_tool(name, arguments, workspace)
        except DocumentError as exc:
            raise ResourceHandlerArgumentError(str(exc)) from exc
        return detail.get("ok") is True, detail
    try:
        detail = skill_reference.call(name, arguments)
    except SkillReferenceArgumentError as exc:
        raise ResourceHandlerArgumentError(str(exc)) from exc
    except SkillReferenceUnavailable as exc:
        return False, {"code": "skill_reference_unavailable", "message": str(exc)}
    return True, detail


__all__ = ["RESOURCE_NAMES", "ResourceHandlerArgumentError", "dispatch_resource"]
