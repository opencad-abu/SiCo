"""MCP handlers for current-session PDK discovery and binding adaptation."""

from __future__ import annotations

from typing import Any

from .circuit_spec_schema import CircuitSpecError
from .pdk_binding import PdkBindings
from .pdk_extensions import DeviceSupportUnavailable, call_extensions
from .pdk_binding_schema import BINDING_NAMES
from .pdk_schema import PDK_NAMES, PdkArgumentError, PdkUnavailable
from .pdk_session import PdkSession


class PdkHandlerArgumentError(ValueError):
    """Raised when a PDK handler receives invalid tool arguments."""


def dispatch_pdk(
    name: str,
    arguments: dict[str, Any],
    *,
    session: PdkSession | None,
    bindings: PdkBindings | None,
    client: Any,
) -> tuple[bool, dict[str, Any]]:
    """Dispatch one PDK-family tool while preserving MCP error semantics."""
    if name not in PDK_NAMES and name not in BINDING_NAMES:
        raise ValueError(f"unknown PDK tool: {name}")

    if name in PDK_NAMES:
        if session is None:
            raise PdkHandlerArgumentError("PDK session is unavailable")
        try:
            return True, session.call(name, arguments)
        except PdkArgumentError as exc:
            raise PdkHandlerArgumentError(str(exc)) from exc
        except PdkUnavailable as exc:
            return False, {"ok": False, "code": exc.code, "message": str(exc)}

    if name == "list_project_extensions":
        try:
            return True, call_extensions(arguments, client)
        except (CircuitSpecError, PdkArgumentError) as exc:
            raise PdkHandlerArgumentError(str(exc)) from exc
        except DeviceSupportUnavailable as exc:
            return False, exc.detail
        except PdkUnavailable as exc:
            return False, {"ok": False, "code": exc.code, "message": str(exc)}

    if bindings is None:
        raise PdkHandlerArgumentError("PDK bindings are unavailable")
    try:
        return True, bindings.bind(arguments)
    except (CircuitSpecError, PdkArgumentError) as exc:
        raise PdkHandlerArgumentError(str(exc)) from exc
    except DeviceSupportUnavailable as exc:
        return False, exc.detail
    except PdkUnavailable as exc:
        return False, {"ok": False, "code": exc.code, "message": str(exc)}


__all__ = ["PdkHandlerArgumentError", "dispatch_pdk"]
