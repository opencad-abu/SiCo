"""History-only paginated revert with a frozen boundary and verified readback."""

import time

from .goals import GoalStopped
from .rpc import RpcError, RpcRejected


def turn_ids(rpc, thread_id):
    """Read one bounded, ordered snapshot; partial histories cannot choose a boundary."""
    cursor, visited, identities = None, set(), []
    deadline = time.monotonic() + 30
    for _ in range(100):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RpcError("History read exceeded its time budget; no automatic replay")
        page = rpc.request("thread/turns/list", {
            "threadId": thread_id, "sortDirection": "asc", "itemsView": "notLoaded",
            "limit": 20, "cursor": cursor,
        }, timeout=min(5, remaining))
        rows = page.get("data")
        if not isinstance(rows, list) or len(rows) > 20:
            raise RpcError("Invalid history page")
        for row in rows:
            if (not isinstance(row, dict) or not isinstance(row.get("id"), str)
                    or not row["id"] or row["id"] in identities
                    or row.get("status") not in {"completed", "interrupted", "failed"}):
                raise RpcError("History has duplicate, invalid or active turns; review required")
            identities.append(row["id"])
        cursor = page.get("nextCursor")
        if cursor is None:
            return identities
        if not isinstance(cursor, str) or not cursor or cursor in visited or not rows:
            raise RpcError("Invalid history cursor")
        visited.add(cursor)
    raise RpcError("History exceeds the page budget; review required")


def checked_thread(rpc, identity):
    thread = rpc.request("thread/read", {"threadId": identity, "includeTurns": False}).get("thread")
    if (not isinstance(thread, dict) or thread.get("id") != identity
            or thread.get("historyMode") != "paginated"):
        raise RpcError("History identity or mode changed; no automatic replay")
    return thread


def revert_history(backend, count):
    if backend.history_mode != "paginated":
        raise RpcRejected("旧格式会话不支持历史回退；历史保留，可继续会话。")
    rpc, identity = backend.runtime.rpc, backend.thread_id
    checked_thread(rpc, identity)
    before = turn_ids(rpc, identity)
    if type(count) is not int or not 1 <= count <= len(before):
        raise RpcRejected("回退回合数超过现有历史；历史未修改。")
    kept, removed = before[:-count], before[-count:]
    if backend.thread_id != identity or backend.cancelled() or backend.stale:
        raise GoalStopped("History operation stopped before dispatch")
    # The journal retains audit evidence even when Codex truncates model context.
    boundary = {"thread_id": identity, "before_turn_id": removed[0],
                "retained_turn_ids": kept, "removed_turn_ids": removed}
    backend._event("codex.history.revert_requested", boundary)
    result = rpc.request("thread/revert", {"threadId": identity, "beforeTurnId": removed[0]})
    thread = result.get("thread")
    if not isinstance(thread, dict) or thread.get("id") != identity:
        raise RpcError("Invalid revert identity; review required, no automatic replay")
    if backend.thread_id != identity or turn_ids(rpc, identity) != kept:
        raise RpcError("Reverted history readback differs; review required, no automatic replay")
    checked_thread(rpc, identity)
    backend._event("codex.history.reverted", boundary)
    backend.native_history.sync(thread)
