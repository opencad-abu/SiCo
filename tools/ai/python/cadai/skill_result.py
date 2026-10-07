"""The single decoder for JSON-valued SKILL calls and their diagnostics."""

import json

from .skill_diagnostics import carry_output, record_output


def call_skill(client, code, *, native=False, context="inspection"):
    """Execute once, decode and record output, including transport failures.

    Use native=True only for an existing native protocol or operations retaining
    ports (GUI/ADE/async). It does not enable capture in a native backend.
    """
    try:
        ok, detail = client.call("eval_skill_native" if native else "eval_skill", {"code": code})
    except Exception as exc:
        # Some native clients raise a structured error instead of returning False.
        # Keep its type/outcome semantics and record evidence before domain mapping.
        record_output(getattr(exc, "data", None))
        raise
    return decode_skill_result(detail, transport_ok=ok, context=context)


def decode_skill_result(detail, *, transport_ok=True, context="inspection"):
    """Decode one reply, retaining business errors and recording capture once.

    Alternate transports pass their success flag here. Failed transport replies
    are not parsed as JSON values. Diagnostics never determine business success.
    """
    if transport_ok:
        ok, result = _decode_value(detail, context)
    else:
        ok, result = False, detail
    result = carry_output(result, detail)
    record_output(result)
    return ok, result


def _decode_value(detail, context):
    if not isinstance(detail, dict):
        return False, {"code": "invalid_result", "message": f"{context} reply must be an object"}
    if detail.get("spooled") or detail.get("value_truncated"):
        # Preserve the legacy inspection/workflow error contracts at this boundary.
        if context == "inspection":
            message = ("inspection result exceeded the inline bridge limit" if detail.get("spooled")
                       else "inspection result was truncated by Virtuoso")
        else:
            message = f"{context} result exceeded the inline limit"
        result = {"code": "result_too_large", "message": message}
        if context == "inspection" and detail.get("spooled"):
            result["detail"] = detail
        return False, result
    value = detail.get("value")
    if not isinstance(value, str):
        owner = "Virtuoso inspection" if context == "inspection" else context
        return False, {"code": "invalid_result", "message": f"{owner} did not return JSON text"}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError) as exc:
        return False, {"code": "invalid_result", "message": f"invalid {context} JSON: {exc}"}
    if not isinstance(parsed, dict):
        return False, {"code": "invalid_result", "message": f"{context} result must be an object"}
    return parsed.get("ok") is True, parsed
