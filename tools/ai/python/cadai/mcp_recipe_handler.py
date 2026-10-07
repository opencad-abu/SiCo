"""MCP adaptation for reference circuit recipes and their device catalog."""

from .circuit_recipe import preview_rc_circuit
from .circuit_gate import preview_gpdk_gate
from .circuit_tools import build_circuit_skill
from .device_catalog import DeviceCatalogUnavailable, query_catalog
from .skill_result import call_skill


def dispatch_preview(name, arguments):
    preview = preview_rc_circuit if name == "preview_rc_circuit" else preview_gpdk_gate
    return True, preview(arguments)


def dispatch_catalog(name, arguments, *, workspace):
    try:
        return True, query_catalog(arguments, workspace)
    except DeviceCatalogUnavailable as exc:
        return False, {"code": "device_catalog_unavailable", "message": str(exc)}


def dispatch_recipe(name, arguments, *, client):
    code = build_circuit_skill(name, arguments)
    return call_skill(client, code, native=True)
