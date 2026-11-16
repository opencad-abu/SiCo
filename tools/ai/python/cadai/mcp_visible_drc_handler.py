"""MCP adapter for the composed visible-window DRC tool."""

from __future__ import annotations

from typing import Any

from .visible_drc import (
    CANCEL_NAME,
    GET_NAME,
    LIST_NAME,
    NAME,
    START_NAME,
    VisibleDrcError,
    VisibleDrcService,
)


def dispatch_visible_drc(
    name: str,
    arguments: dict[str, Any],
    *,
    client,
    get_service=None,
):
    service = get_service() if get_service is not None else VisibleDrcService(client=client)
    try:
        if name == NAME:
            return service.check(arguments)
        if name == START_NAME:
            return True, service.start(arguments)
        if name == GET_NAME:
            return True, service.get(
                arguments["job_ref"], arguments.get("after", 0)
            )
        if name == CANCEL_NAME:
            return True, service.cancel(arguments["job_ref"])
        if name == LIST_NAME:
            return True, service.list_jobs(arguments)
    except VisibleDrcError as exc:
        return False, {"ok": False, "code": exc.code, "message": str(exc)}
    except KeyError as exc:
        raise ValueError("visible DRC request is missing a field") from exc
    raise ValueError(f"unknown visible DRC tool: {name}")


__all__ = ["dispatch_visible_drc"]
