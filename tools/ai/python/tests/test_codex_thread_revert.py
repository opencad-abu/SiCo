"""Revert chooses one complete history boundary and never retries a mutation."""

from types import SimpleNamespace

import pytest

from sico.codex.rpc import RpcError, RpcRejected
from sico.codex.thread_revert import revert_history, revert_to_turn


class History:
    def __init__(self, count=23):
        self.ids = [str(i) for i in range(count)]
        self.calls = []
        self.events = []
        self.synced = []
        self.backend = SimpleNamespace(runtime=SimpleNamespace(rpc=self), thread_id="thread",
            history_mode="paginated", cancelled=lambda: False, stale=False,
            _event=lambda *args: self.events.append(args),
            native_history=SimpleNamespace(sync=self.synced.append))
        self.fault = None

    def request(self, method, params, **kwargs):
        self.calls.append((method, params))
        assert params["threadId"] == "thread"
        if method == "thread/read":
            return {"thread": {"id": "thread", "historyMode": "paginated"}}
        if method == "thread/turns/list":
            assert params["sortDirection"] == "asc" and params["itemsView"] == "notLoaded"
            start = int(params["cursor"] or 0)
            values = self.ids[start:start + params["limit"]]
            return {"data": [{"id": i, "status": "completed"} for i in values],
                    "nextCursor": str(start + len(values)) if start + len(values) < len(self.ids) else None}
        assert method == "thread/revert"
        assert self.events[-1][0] == "codex.history.revert_requested"
        self.ids = self.ids[:self.ids.index(params["beforeTurnId"])]
        if self.fault == "timeout":
            raise RpcError("Timed out after mutation")
        if self.fault == "readback":
            self.ids.append("unexpected")
        return {"thread": {"id": "wrong" if self.fault == "identity" else "thread",
                           "historyMode": "paginated"}}


@pytest.mark.parametrize("count", [1, 3, 23])
def test_revert_reads_all_pages_and_records_exact_boundary(count):
    h = History()
    revert_history(h.backend, count)
    assert h.ids == [str(i) for i in range(23 - count)]
    assert h.events[-1][0] == "codex.history.reverted"
    assert h.events[-1][1]["before_turn_id"] == str(23 - count)
    assert len(h.synced) == 1
    assert len([m for m, _ in h.calls if m == "thread/revert"]) == 1


@pytest.mark.parametrize("existing,count", [(0, 1), (1, 2), (3, 0), (3, True)])
def test_invalid_count_never_changes_history(existing, count):
    h = History(existing)
    with pytest.raises(RpcRejected):
        revert_history(h.backend, count)
    assert all(m != "thread/revert" for m, _ in h.calls)


@pytest.mark.parametrize("fault", ["timeout", "readback", "identity"])
def test_uncertain_revert_requires_review_and_is_never_retried(fault):
    h = History()
    h.fault = fault
    with pytest.raises(RpcError):
        revert_history(h.backend, 1)
    assert len([m for m, _ in h.calls if m == "thread/revert"]) == 1
    assert [event[0] for event in h.events] == ["codex.history.revert_requested"]
    assert not h.synced


def test_legacy_history_never_calls_removed_rpc():
    h = History()
    h.backend.history_mode = "legacy"
    with pytest.raises(RpcRejected, match="旧格式"):
        revert_history(h.backend, 1)
    assert not h.calls


@pytest.mark.parametrize("turn_id,kept", [
    ("0", []),
    ("10", [str(index) for index in range(10)]),
    ("22", [str(index) for index in range(22)]),
])
def test_revert_to_turn_uses_the_exact_visible_boundary(turn_id, kept):
    h = History()
    revert_to_turn(h.backend, turn_id)
    assert h.ids == kept
    assert h.events[-1][0] == "codex.history.reverted"
    assert h.events[-1][1]["before_turn_id"] == turn_id
    assert [method for method, _ in h.calls].count("thread/revert") == 1


@pytest.mark.parametrize("turn_id", ["missing", "", " 0", None, 1, "x" * 201])
def test_revert_to_unknown_or_invalid_boundary_never_changes_history(turn_id):
    h = History()
    with pytest.raises(RpcRejected):
        revert_to_turn(h.backend, turn_id)
    assert all(method != "thread/revert" for method, _ in h.calls)


def test_revert_to_turn_uncertain_result_is_never_replayed():
    h = History()
    h.fault = "readback"
    with pytest.raises(RpcError):
        revert_to_turn(h.backend, "10")
    assert [method for method, _ in h.calls].count("thread/revert") == 1
    assert [event[0] for event in h.events] == ["codex.history.revert_requested"]


@pytest.mark.parametrize("damage", ["duplicate", "active", "cursor"])
def test_incomplete_or_ambiguous_page_cannot_select_boundary(damage):
    h = History()
    original = h.request
    def damaged(method, params, **kwargs):
        result = original(method, params, **kwargs)
        if method == "thread/turns/list":
            if damage == "duplicate":
                result["data"][1] = result["data"][0]
            elif damage == "active":
                result["data"][0]["status"] = "inProgress"
            else:
                result["nextCursor"] = "20"
        return result
    h.backend.runtime.rpc = SimpleNamespace(request=damaged)
    with pytest.raises(RpcError):
        revert_history(h.backend, 1)
    assert all(m != "thread/revert" for m, _ in h.calls)
