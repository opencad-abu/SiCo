"""Deterministic model substitute for development; never presented as a real model."""

from __future__ import annotations

import json
import uuid

from ..core.contracts import Cancelled, ModelTurn, TextDelta, ToolCall


class ContextDemoProvider:
    label = "模拟模型（无模型请求）"

    def stream(self, system, messages, tools, cancelled):
        if cancelled():
            raise Cancelled()
        if messages[-1]["role"] != "tool":
            prompt = messages[-1]["content"].lower()
            method = "get_context"
            if "history" in prompt or "历史" in prompt:
                method = "read_ade_history"
            elif "ade" in prompt or "输出配置" in prompt:
                method = "read_ade_setup"
            method = method if any(tool["name"] == method for tool in tools) else "get_context"
            yield TextDelta("[模拟模型] 正在读取已绑定的 Virtuoso 上下文。\n")
            yield ModelTurn(
                "[模拟模型] 正在读取已绑定的 Virtuoso 上下文。\n",
                (ToolCall(uuid.uuid4().hex, method, {}),),
            )
        else:
            result = json.loads(messages[-1]["content"])
            status = "已读取" if result.get("status") == "ok" else "未完成"
            text = "[模拟模型] 工具返回数据" + status + "。"
            if result.get("summary"):
                text += "\n\n" + result["summary"]
            data = result.get("data")
            if isinstance(data, dict) and isinstance(data.get("cellview"), dict):
                target = " / ".join(
                    str(data["cellview"].get(key) or "未提供") for key in ("lib", "cell", "view")
                )
                text += "\n\n设计目标：" + target
            if result.get("truncated"):
                text += "\n\n结果已截断，完整数据已归档。"
            yield TextDelta(text)
            yield ModelTurn(text)


class ScriptedProvider:
    """An iterable of provider turns, suitable for repeatable fault injection."""

    def __init__(self, turns):
        self.turns = iter(turns)
        self.requests = []

    def stream(self, system, messages, tools, cancelled):
        self.requests.append(
            json.loads(json.dumps({"system": system, "messages": messages, "tools": tools}))
        )
        if cancelled():
            raise Cancelled()
        turn = next(self.turns)
        if isinstance(turn, Exception):
            raise turn
        yield from turn
