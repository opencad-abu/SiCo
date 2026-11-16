"""MCP adapter for the read-only DRC rule catalog."""

from .drc_rules import NAME, DrcRulesError, build_catalog


def dispatch_drc_rules(name, arguments):
    if name != NAME:
        raise ValueError(f"unknown DRC rules tool: {name}")
    try:
        return True, build_catalog(arguments)
    except DrcRulesError as exc:
        return False, {"ok": False, "code": exc.code, "message": str(exc)}


__all__ = ["dispatch_drc_rules"]
