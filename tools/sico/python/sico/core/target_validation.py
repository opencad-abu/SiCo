"""Choose the captured context read and judge its structured target receipt."""

from .contracts import NeedsReconcile


class TargetUnconfirmed(RuntimeError):
    """A read failed before answer dispatch; the captured source remains owned."""


def context_method(context, schemas, *, audit=False):
    if audit and any(context.snapshot.get(k) for k in ("window_ref", "ade_session", "history", "test")):
        return "get_context"
    names = {tool["name"] for tool in schemas}
    capabilities = context.snapshot.get("capabilities", [])
    for method in ("get_project_context", "get_entry_context"):
        if method in capabilities and method in names:
            return method
    return "get_context"


def validate_target_receipt(value):
    data = value.get("data") if isinstance(value, dict) else None
    code = data.get("code") if isinstance(data, dict) else None
    if isinstance(value, dict) and (
            value.get("status") in {"context_unavailable", "read_failed"}
            or code == "context_unavailable"):
        raise TargetUnconfirmed("来源暂时无法核验，答复已保留，等待重新核验")
    if not isinstance(value, dict) or value.get("status") != "ok":
        raise NeedsReconcile("Captured target is no longer available")
    if not isinstance(data, dict) or data.get("valid") is False:
        raise NeedsReconcile("Captured target is invalid")
