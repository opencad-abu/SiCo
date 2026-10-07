"""MCP handlers for template catalogs, circuit adaptation, symbols, and placement."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .circuit_generic import GENERIC_NAMES, call_generic
from .circuit_spec_schema import CircuitSpecError
from .pdk_schema import PdkUnavailable
from .template_catalog import call_template
from .template_circuit import TOOL_NAME as TEMPLATE_CIRCUIT_NAME
from .template_circuit import prepare_template
from .template_placement import TOOL_NAMES as TEMPLATE_PLACEMENT_NAMES
from .template_placement import preview_placement
from .template_prepare import TOOL_NAME as TEMPLATE_PREPARE_NAME
from .template_prepare import call_prepare
from .template_schema import TOOLS as TEMPLATE_CATALOG_TOOLS
from .template_schema import TemplateError, TemplateUnavailable
from .template_symbol import TOOL_NAMES as TEMPLATE_SYMBOL_NAMES
from .template_symbol import call_symbol

TEMPLATE_CATALOG_NAMES = frozenset(tool["name"] for tool in TEMPLATE_CATALOG_TOOLS)


class TemplateHandlerArgumentError(ValueError):
    """Raised when a template-family request violates its input contract."""


def dispatch_template(
    name: str,
    arguments: dict[str, Any],
    *,
    workspace: Path | None,
    client: Any,
    pdk_bindings: Any = None,
) -> tuple[bool, dict[str, Any]]:
    """Dispatch one template-family tool with its stable MCP error mapping."""
    if name in TEMPLATE_CATALOG_NAMES:
        extraction = name == "extract_circuit_templates"
        try:
            detail = call_template(
                name,
                arguments,
                workspace=workspace,
                client=client if extraction else None,
            )
        except TemplateError as exc:
            raise TemplateHandlerArgumentError(str(exc)) from exc
        except TemplateUnavailable as exc:
            code = ("template_extraction_unavailable" if extraction
                    else "template_catalog_unavailable")
            return False, {"code": code, "message": str(exc)}
        return detail.get("ok") is True if extraction else detail.get("ok") is not False, detail

    if name in TEMPLATE_SYMBOL_NAMES:
        try:
            detail = call_symbol(name, arguments, workspace=workspace, client=client)
        except TemplateError as exc:
            raise TemplateHandlerArgumentError(str(exc)) from exc
        except TemplateUnavailable as exc:
            return False, {"code": "template_symbol_unavailable", "message": str(exc)}
        return detail.get("ok") is True, detail

    if name == TEMPLATE_PREPARE_NAME:
        try:
            detail = call_prepare(name, arguments, workspace=workspace, pdk_bindings=pdk_bindings)
        except TemplateError as exc:
            raise TemplateHandlerArgumentError(str(exc)) from exc
        except TemplateUnavailable as exc:
            return False, {"code": "template_prepare_unavailable", "message": str(exc)}
        return detail.get("status") not in {"unavailable", "invalid_request"}, detail

    if name == TEMPLATE_CIRCUIT_NAME or name in GENERIC_NAMES:
        try:
            detail = (
                prepare_template(arguments, workspace=workspace)
                if name == TEMPLATE_CIRCUIT_NAME
                else call_generic(
                    name,
                    arguments,
                    workspace=workspace,
                    pdk_bindings=pdk_bindings,
                )
            )
        except CircuitSpecError as exc:
            raise TemplateHandlerArgumentError(str(exc)) from exc
        except TemplateUnavailable as exc:
            return False, {"code": "template_circuit_unavailable", "message": str(exc)}
        return True, detail

    if name in TEMPLATE_PLACEMENT_NAMES:
        try:
            detail = preview_placement(
                arguments,
                workspace=workspace,
                pdk_bindings=pdk_bindings,
            )
        except CircuitSpecError as exc:
            raise TemplateHandlerArgumentError(str(exc)) from exc
        except TemplateUnavailable as exc:
            return False, {"code": "template_placement_unavailable", "message": str(exc)}
        except PdkUnavailable as exc:
            return False, {"code": exc.code, "message": str(exc)}
        return True, detail

    raise ValueError(f"unknown template tool: {name}")


__all__ = [
    "TEMPLATE_CATALOG_NAMES",
    "TemplateHandlerArgumentError",
    "dispatch_template",
]
