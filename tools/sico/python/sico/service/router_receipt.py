"""Select, validate and format routed request evidence outside Qt."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from ..core.contracts import BoundContext, NeedsReconcile

MAX_RECEIPT_TEXT = 256 * 1024
CONTEXT_FIELDS = ("instance_id", "generation", "target_id")


@dataclass(frozen=True)
class RouterReceiptView:
    session_id: str
    runtime_id: str
    context: BoundContext
    request_id: str
    title: str
    text: str


def latest_router_request(events, context, session_id):
    identity = {key: getattr(context, key) for key in CONTEXT_FIELDS}
    task_id, task_context, latest = None, {}, None
    for event in events:
        if event.get("session_id") != session_id:
            continue
        payload = event["payload"]
        if event["kind"] == "task.started":
            task_id, task_context = event.get("task_id"), payload.get("context", {})
        elif event["kind"] == "router.status" and payload.get("session_id") == session_id:
            # Earlier router events inherit instance/generation from their task.
            source = task_context if event.get("task_id") == task_id else {}
            bound = {key: payload.get(key, source.get(key)) for key in CONTEXT_FIELDS}
            if bound == identity and payload.get("task_id") == event.get("task_id"):
                latest = payload
    if latest is None:
        raise ValueError("当前来源没有可查询的路由回执")
    request_id = latest.get("request_id")
    if not isinstance(request_id, str) or not re.fullmatch(r"[0-9a-f]{32}", request_id):
        raise ValueError("路由请求标识无效")
    return latest


def validate_router_receipt(receipt, request, context, session_id):
    expected = {key: getattr(context, key) for key in CONTEXT_FIELDS}
    expected.update(session_id=session_id, request_id=request["request_id"])
    if request.get("router_id"):
        expected["router_id"] = request["router_id"]
    if not isinstance(receipt, dict) or any(receipt.get(key) != value
                                            for key, value in expected.items()):
        raise NeedsReconcile("路由回执与查询的会话、实例或目标不匹配")


def router_receipt_view(receipt, context, session_id, runtime_id):
    text = json.dumps(receipt, ensure_ascii=False, indent=2, allow_nan=False)
    encoded = text.encode("utf-8")
    if len(encoded) > MAX_RECEIPT_TEXT:
        notice = "\n[回执过长，已截断显示；完整记录保留在后台]"
        prefix = encoded[:MAX_RECEIPT_TEXT - len(notice.encode("utf-8"))]
        text = prefix.decode("utf-8", errors="ignore") + notice
    return RouterReceiptView(
        session_id, runtime_id, BoundContext.from_record(context.record()),
        receipt["request_id"], "路由回执 / " + receipt["request_id"][:8], text,
    )
