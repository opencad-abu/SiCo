"""Track whether the latest model step produced a usable public response."""

from .native_projection import TOOLS

EMPTY_RESPONSE = (
    "模型已结束响应，但没有提供有效答复或后续操作，任务尚未完成。"
    "已有工具回执已保留；请核对后使用会话的“继续”入口，不会自动重跑已有操作。"
)


class TurnResponse:
    def __init__(self, *, compact=False):
        self.compact = compact
        self.usable = False

    def observe(self, method, params):
        if method not in {"item/started", "item/completed"}:
            return
        item = params["item"]
        kind = item.get("type")
        if kind in {"reasoning", "mcpToolCall", *TOOLS}:
            # Earlier commentary cannot complete a later, empty model step.
            self.usable = False
        elif method == "item/completed" and kind in {"agentMessage", "plan"}:
            self.usable = bool(item.get("text", "").strip())
        elif method == "item/completed" and kind == "contextCompaction" and self.compact:
            self.usable = True
        elif kind == "collabAgentToolCall":
            self.usable = True

    def interaction(self):
        # An explicit user/child handoff is a legitimate end without prose.
        self.usable = True
