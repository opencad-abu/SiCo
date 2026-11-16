"""Real paginated history boundaries survive reopen without changing files or replay."""

import pytest

from sico.codex.backend import CodexBackend
from sico.codex.thread_revert import turn_ids
from sico.codex.rpc import AppServer
from sico.storage.journal import SessionJournal
from test_agent_codex_native import (
    CONTEXT, answer, gateway as gateway, native_codex as native_codex, registry, settings,
)
from test_codex_thread_operations_runtime import operate


@pytest.mark.parametrize("count", [1, 2, 3])
def test_revert_boundary_and_reopen(tmp_path, gateway, native_codex, count, monkeypatch):
    original, cursors = AppServer.request, []
    def paged(self, method, params, **kwargs):
        if method == "thread/turns/list":
            cursors.append(params.get("cursor"))
            params = {**params, "limit": 2}
        return original(self, method, params, **kwargs)
    monkeypatch.setattr(AppServer, "request", paged)
    retained_file = tmp_path / "keep.txt"
    retained_file.write_text("Work done by previous turns stays present")
    with SessionJournal(tmp_path, "revert") as journal:
        backend = CodexBackend(settings(gateway, native_codex, "native"), registry([]), journal, CONTEXT)
        try:
            for n in range(3):
                gateway.responses.put(answer(str(n)))
                assert list(backend.run(str(n)))[-1]["kind"] == "task.completed"
            identity = backend.thread_id
            before = turn_ids(backend.runtime.rpc, identity)
            assert len(before) == 3
            assert any(cursor is not None for cursor in cursors)
            original_events = journal.events()
            operate(backend, {"kind": "rollback", "num_turns": count, "history_only": True})
            assert turn_ids(backend.runtime.rpc, identity) == before[:-count]
            assert journal.events()[:len(original_events)] == original_events
            assert retained_file.read_text() == "Work done by previous turns stays present"
            assert len(gateway.requests) == 3
            backend.close()
            backend = CodexBackend(settings(gateway, native_codex, "native"), registry([]), journal, CONTEXT)
            backend.resume_thread()
            assert backend.thread_id == identity
            assert turn_ids(backend.runtime.rpc, identity) == before[:-count]
            assert len(gateway.requests) == 3
        finally:
            backend.close()
