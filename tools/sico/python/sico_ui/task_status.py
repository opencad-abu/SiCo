"""Task queue status labels, colors and tooltip text."""

from __future__ import annotations

from datetime import datetime, timezone

from PyQt5.QtGui import QColor

from .theme import LIGHT_PALETTE, active_palette

STATUS = {
    "queued": "已接收 · 排队中",
    "executing": "执行中",
    "waiting_user": "等待答复",
    "completed": "已完成",
    "cancelled": "已取消",
    "failed": "失败",
    "needs_reconcile": "中断 · 待核对",
    "not_started": "未执行 · 不自动重放",
}
TREE_STATUS = {
    "active": "进行中",
    "blocked": "阻塞",
    "reported": "已报告",
    "in_progress": "进行中",
    "planned": "计划中",
    "closed": "已关闭",
    "idle": "空闲",
}
STATUS_SHORT = {
    "queued": "排队",
    "executing": "执行中",
    "waiting_user": "等待",
    "completed": "完成",
    "cancelled": "取消",
    "failed": "失败",
    "needs_reconcile": "待核对",
    "not_started": "未执行",
}
def _colors(palette):
    return {
        "queued": QColor(palette.status_queued),
        "executing": QColor(palette.status_executing),
        "waiting_user": QColor(palette.status_waiting),
        "completed": QColor(palette.status_completed),
        "cancelled": QColor(palette.status_queued),
        "failed": QColor(palette.status_failed),
        "needs_reconcile": QColor(palette.status_failed),
        "not_started": QColor(palette.status_queued),
        "active": QColor(palette.status_executing),
        "in_progress": QColor(palette.status_executing),
        "blocked": QColor(palette.status_failed),
        "reported": QColor(palette.status_completed),
        "planned": QColor(palette.status_queued),
        "closed": QColor(palette.status_queued),
        "idle": QColor(palette.status_queued),
    }


# Historical light-theme mapping for direct callers; live widgets ask for the
# active palette through status_color().
STATUS_COLORS = _colors(LIGHT_PALETTE)


def status_color(status):
    """One task/execution state colour from the active palette."""
    return _colors(active_palette()).get(status)


def execution_status(execution, request):
    if request:
        state = request.get("state")
        if state == "queued":
            return "排队" + str(request.get("queue_position") or ""), state
        return {
            "running": "执行中",
            "cancel_requested": "取消中",
            "timed_out_unknown": "待确认",
        }.get(state, "Virtuoso"), state
    status = str(execution.get("status", ""))
    return STATUS_SHORT.get(status, status), status


def router_request_label(request):
    state = request.get("state")
    if state == "queued":
        return f"等待 Virtuoso · 第 {request['queue_position']} 位"
    return {
        "running": "Virtuoso 执行中",
        "cancel_requested": "正在取消等待中的请求",
        "timed_out_unknown": "Virtuoso 执行结果待确认",
    }.get(state, "")


def router_request_short_label(request):
    """Keep the live state readable in the narrow task tree."""
    state = request.get("state")
    if state == "queued":
        return f"Virtuoso · 第{request['queue_position']}位"
    return {
        "running": "Virtuoso执行中",
        "cancel_requested": "Virtuoso取消中",
        "timed_out_unknown": "Virtuoso待确认",
    }.get(state, "Virtuoso")


def router_tooltip(router):
    if not router:
        return ""
    labels = {
        "idle": "空闲", "running": "执行中", "queued": "排队中",
        "blocked_unknown": "执行结果待确认", "router_unavailable": "未连接",
    }
    lines = ["Virtuoso：" + labels.get(router["state"], router["state"]),
             "实例：" + router["instance_id"], "连接代次：" + router["generation"]]
    owner = router.get("owner")
    if owner:
        lines.extend(["占用请求：" + owner["request_id"],
                      "所属会话：" + owner.get("session_id", "未关联会话"),
                      "执行目标：" + owner.get("target_id", "未关联目标")])
        started = owner.get("started_at")
        if isinstance(started, (int, float)):
            lines.append(
                "开始时间：" + datetime.fromtimestamp(started).strftime("%Y-%m-%d %H:%M:%S")
            )
    lines.append(f"实例等待请求：{router['queued']}")
    for request in router["requests"]:
        lines.append("本会话请求：" + request["request_id"] + " · " + router_request_label(request))
    return "\n".join(lines)


def session_status(state):
    if state["fault"]:
        return "会话故障"
    if state["closing"]:
        return "正在结束会话…" if state["busy"] else "会话已结束"
    if state["cancelling"]:
        return "正在取消…"
    router = state.get("router") or {}
    if router.get("state") == "blocked_unknown":
        return "Virtuoso 执行结果待确认"
    if router.get("state") == "router_unavailable":
        return "Virtuoso 未连接"
    if state["task"].get("status") == "needs_reconcile":
        return "任务已中断 · 待核对"
    if state["busy"]:
        if state["task"].get("waiting_audits"):
            return "等待答复" + (
                f"；待办 {state['pending']} 条" if state["pending"] else ""
            )
        supplementary = len(state["task"].get("supplementary_audits") or ())
        if supplementary:
            return f"执行中；补充事项 {supplementary} 条"
        requests = [request for request in router.get("requests", [])
                    if request.get("task_id") and request["task_id"] == state["task"].get("id")]
        if requests:
            label = router_request_label(requests[0])
            return label + (f"；待办 {state['pending']} 条" if state["pending"] else "")
        activity = state["task"].get("activity") or {}
        label = activity.get("label") or "正在处理任务"
        tool = activity.get("tool")
        if tool:
            label += f"：{tool}"
        turn = activity.get("turn")
        if isinstance(turn, int) and turn > 0:
            label += f"（第 {turn} 轮）"
        started = activity.get("started_at")
        if isinstance(started, str):
            try:
                if started.endswith("Z"):
                    started = started[:-1] + "+00:00"
                elapsed = max(
                    0,
                    int(
                        (
                            datetime.now(timezone.utc) - datetime.fromisoformat(started)
                        ).total_seconds()
                    ),
                )
                label += f" · {elapsed}s"
                if activity.get("phase") == "waiting_model" and elapsed >= 60:
                    label += " · 尚未收到可用答复"
            except ValueError:
                pass
        return label + (f"；队列 {state['pending']} 条" if state["pending"] else "")
    status = state["task"].get("status")
    label = "未执行" if status == "not_started" else STATUS.get(status, "就绪")
    if state["paused"] and state["pending"]:
        label += f"；待办 {state['pending']} 条（已暂停）"
    return label
