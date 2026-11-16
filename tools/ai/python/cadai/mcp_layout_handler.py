"""Draft MCP adapter for the project-scoped layout backend service.

The handler is directly tested but is not registered in the global MCP tool
surface until the catalog budget review is complete.
"""

from __future__ import annotations

from typing import Any

from .layout.host import HostError
from .layout.import_plan import ImportPlanError
from .layout.protocol import BackendProcessError, RpcError
from .layout.registry import ManifestError
from .layout.service import LayoutServiceError
from .layout_schema import LAYOUT_NAMES


class LayoutHandlerArgumentError(ValueError):
    pass


def dispatch_layout(
    name: str,
    arguments: dict[str, Any],
    *,
    get_service,
) -> tuple[bool, dict[str, Any]]:
    if name not in LAYOUT_NAMES:
        raise LayoutHandlerArgumentError("unknown layout tool")
    try:
        service = get_service()
        if name == "list_layout_backends":
            return True, {"ok": True, **service.list_backends()}
        if name == "preview_layout_job":
            return True, {
                "ok": True,
                "backend_id": arguments["backend_id"],
                "validation": service.preview(arguments["backend_id"], arguments["request"]),
            }
        if name == "start_layout_job":
            return True, {
                "ok": True,
                **service.start(arguments["backend_id"], arguments["request"]),
            }
        if name == "get_layout_job":
            return True, {"ok": True, **service.status(arguments["job_id"])}
        if name == "cancel_layout_job":
            return True, {"ok": True, **service.cancel(arguments["job_id"])}
        if name == "plan_layout_import":
            return True, {
                "ok": True,
                "plan": service.plan_import(
                    arguments["job_id"],
                    target_library=arguments["target_library"],
                    target_cell=arguments["target_cell"],
                    target_view=arguments["target_view"],
                    layer_map=arguments["layer_map"],
                ),
            }
        if name == "get_layout_import_status":
            return True, {
                "ok": True,
                "status": service.import_status(arguments["plan_file"]),
            }
    except KeyError as exc:
        raise LayoutHandlerArgumentError("layout request is missing a field") from exc
    except (LayoutServiceError, ManifestError, HostError, BackendProcessError) as exc:
        return False, {"ok": False, "code": "layout_backend_error", "message": str(exc)}
    except ImportPlanError as exc:
        return False, {"ok": False, "code": "layout_import_plan_error",
                       "message": str(exc)}
    except RpcError as exc:
        return False, {"ok": False, "code": "layout_backend_error", "message": str(exc),
                       "backend_code": exc.code}
    except TimeoutError as exc:
        return False, {"ok": False, "code": "layout_backend_timeout", "message": str(exc)}
    raise LayoutHandlerArgumentError("unsupported layout tool")


__all__ = ["LayoutHandlerArgumentError", "dispatch_layout"]
