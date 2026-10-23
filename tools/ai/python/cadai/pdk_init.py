"""Session-scoped PDK library initialization: one authoritative ensure entry."""

from __future__ import annotations

from .pdk_builtin import LIBRARIES as BUILTIN_LIBRARIES
from .pdk_schema import PdkUnavailable, skill_string
from .pdk_selection import BASE_LIBRARIES, DESIGN_TOOLS

BUILTIN_NAMES = frozenset(BASE_LIBRARIES) | frozenset(BUILTIN_LIBRARIES)


class PdkInit:
    """Ensure libInit runs at least once per session; report, never guess."""

    def __init__(self, bridge):
        self.bridge = bridge
        self.records = {}

    def ensure(self, library):
        if not isinstance(library, str) or not library.strip():
            raise PdkUnavailable("pdk_init_library_missing",
                                 "Select a PDK library or pass library")
        library = library.strip()
        if library in BUILTIN_NAMES:
            return {"ok": True, "library": library,
                    "status": "not_required_builtin", "initialized": False, "cached": False}
        saved = self.records.get(library)
        if saved is not None:
            return {**saved, "cached": True}
        result = self.bridge.call("aiPdkInitializeLibrary(" + skill_string(library) + ")")
        if not isinstance(result, dict) or result.get("ok") is not True:
            code = result.get("code") if isinstance(result, dict) else None
            message = result.get("message") if isinstance(result, dict) else None
            raise PdkUnavailable(code or "pdk_init_failed",
                                 message or "PDK libInit initialization failed")
        record = {
            "ok": True, "library": library,
            "status": result.get("status") or "initialized",
            "init_file": result.get("init_file"),
            "init_path": result.get("init_path"),
            "initialized": bool(result.get("initialized")),
            "cached": bool(result.get("cached")),
        }
        self.records[library] = record
        return record


def with_init(preparation, result):
    """Report initialization state beside a resolved selection; reads stay readable."""
    if not preparation.selected:
        return result
    try:
        result["pdk_init"] = preparation.init.ensure(preparation.selected)
    except PdkUnavailable as exc:
        result["pdk_init"] = {
            "ok": False, "library": preparation.selected,
            "code": exc.code, "message": str(exc), "next_action": "init_pdk",
        }
    return result


def guard_failure(preparation, info):
    """Design tools block when the selected library cannot be initialized."""
    state = info.get("pdk_init") or {}
    if state.get("ok") is not False:
        return None
    return {
        "ok": False, "library": preparation.selected,
        "code": state.get("code", "pdk_init_failed"),
        "message": state.get("message", "PDK initialization is required"),
        "selection_required": False,
        "next_action": "init_pdk（初始化失败/不可证明；修复后重试，不猜路径）",
    }


def guard(preparation, name):
    """Design-tool guard: resolve selection, then require PDK initialization."""
    if name not in DESIGN_TOOLS or preparation.raw_context is None:
        return None
    info = preparation.inspect({"view": preparation.selection_view})
    if info["selection_required"]:
        return info
    return guard_failure(preparation, info)


def ensure_selected(preparation, args):
    library = args.get("library") or preparation.selected
    return preparation.init.ensure(library)


__all__ = ["BUILTIN_NAMES", "PdkInit", "ensure_selected", "guard", "guard_failure",
           "with_init"]
