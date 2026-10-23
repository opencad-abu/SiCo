"""Bounded display summaries for circuit tool receipts."""

import json


def circuit_summary(name, data):
    """Keep continuation information available even when the full plan is an artifact."""
    from .project import operation_summary

    if name in {"execute_circuit_operation", "get_circuit_operation"}:
        return operation_summary(data)
    if data.get("code") == "task_source_mismatch":
        return "该任务属于其他逻辑会话或已失效的绑定代次；请沿用已确认的执行模式建立新任务，再只读检查目标。原任务和写请求不会重放。"
    if name == "bind_pdk_device" and data.get("ok"):
        # Keep the binding token in structured tool data for the agent, but do
        # not repeat this implementation detail in user-facing summaries.
        return "器件完整参数已保存在后端，后续步骤会自动继续；无需用户提供内部信息。"
    if data.get("code", "").startswith("device_support_"):
        target = data.get("target", {})
        return ("器件 " + "/".join(target.get(k, "") for k in ("library", "cell"))
                + " 的自动参数处理尚未通过检查。请读取具体 CDF 和内置回调执行器诊断，"
                + "区分回调语法、过程未加载、参数约束和上下文变化；无需用户注册适配器。")
    if name in {"preview_circuit_spec", "preview_circuit_geometry"}:
        summary = {k: data[k] for k in (
            "next_action", "geometry_preview_ready", "preview_digest",
            "geometry_plan_digest", "user_input_required",
        ) if k in data}
        # The full plan usually travels as an artifact, so echo the acceptance
        # numbers that reviewers ask for (and that the model must quote).
        routing = (data.get("plan") or {}).get("routing")
        if isinstance(routing, dict):
            summary["routing"] = {
                "mode": routing.get("mode"),
                "engine": routing.get("engine"),
                "edges": routing.get("edges"),
                "pg_style": routing.get("pg_style"),
                "pg_stub_terminals": routing.get("pg_stub_terminals"),
                "metrics": routing.get("metrics"),
                "fallbacks": len(routing.get("fallbacks") or []),
                "junctions": len(routing.get("junctions") or []),
                "interior_escapes": len(routing.get("interior_escapes") or []),
            }
        review = (data.get("plan") or {}).get("layout_review")
        if review:
            summary["layout_review"] = [row.get("code") for row in review]
        return json.dumps(summary, ensure_ascii=False)
    if name in {"prepare_circuit_edit", "execute_circuit_edit"}:
        review = data.get("review") or {}
        summary = {k: data[k] for k in (
            "ok", "stage", "next_action", "user_input_required", "prepare_ref",
            "circuit_ref", "geometry_plan_digest", "review_digest", "target",
            "saved", "readback_verified", "check", "user_approved", "edited", "error",
        ) if k in data}
        if review:
            summary["review"] = {
                "mode": review.get("mode"),
                "instances": len(review.get("instances") or []),
                "ports": len(review.get("ports") or []),
                "wires": review.get("wires"),
            }
        if data.get("selection_question"):
            summary["selection_question"] = data["selection_question"]
        return json.dumps(summary, ensure_ascii=False)
    if name == "preview_template_placement":
        report = data.get("report") or {}
        return json.dumps({
            "rows": [row.get("members") for row in report.get("rows") or []],
            "mirrored_pairs": [
                [pair.get("a"), pair.get("b")] for pair in report.get("mirrored_pairs") or []
            ],
            "spacing": report.get("spacing"),
            "ports": report.get("ports"),
            "port_rows": report.get("port_rows"),
            "gaps": [gap.get("code") for gap in report.get("gaps") or []],
            "geometry_review": report.get("geometry_review"),
            "placement_digest": data.get("placement_digest"),
        }, ensure_ascii=False)
    return ""


__all__ = ["circuit_summary"]
