"""Settings receipts and archive reads stay bound to the original task delivery."""

from types import SimpleNamespace

import pytest
from agent_sessions_harness import DesktopSessions
from sico.codex.rpc import RpcError, RpcRejected
from sico.service.controller import SessionController
from sico.service.input_details import input_detail
from sico.service.replay import connection, prepare_preview, prepare_replay
from sico.storage.input_assets import prepare_inputs
from sico.storage.journal import SessionJournal
from test_agent_codex_native import CONTEXT
from test_codex_turn_inputs import PNG, backend


@pytest.mark.parametrize("outcome", ["accepted", "rejected", "unconfirmed", "not_sent",
                                      "cancelled", "cancelled_after_inventory"])
def test_settings_receipt_never_replays_or_promotes_failed_intent(tmp_path, monkeypatch, outcome):
    calls = []
    with SessionJournal(tmp_path, "receipt") as journal:
        loop = backend(journal)
        from codex_thread_fixture import bind_thread
        bind_thread(loop)
        options = loop.turn_options.freeze({"mode": "plan"})

        def request(method, params, **kwargs):
            calls.append(method)
            if method == "turn/start":
                if outcome == "rejected":
                    raise RpcRejected("Invalid schema")
                if outcome == "unconfirmed":
                    raise RpcError("Lost receipt")
                return {"turn": {"id": "turn"}}
            return {}

        def finish(_turn, _cancelled):
            loop._finish("completed")
            yield from loop._drain()

        monkeypatch.setattr(loop, "_ensure_runtime", lambda: None)
        inventoried = []
        monkeypatch.setattr(loop.turn_options, "status", lambda: inventoried.append(True))
        monkeypatch.setattr(loop, "_turn", finish)
        loop.runtime = SimpleNamespace(rpc=SimpleNamespace(request=request), close=lambda: None)
        inputs = []
        if outcome == "not_sent":
            path = tmp_path / "picture.png"
            path.write_bytes(PNG)
            inputs = prepare_inputs(journal, [{"type": "localImage", "path": str(path)}])
            (journal.root / inputs[0]["asset"]["path"]).write_bytes(b"broken")
        def cancelled():
            return outcome == "cancelled" or (
                outcome == "cancelled_after_inventory" and bool(inventoried))
        events = list(loop.run("Read selected inputs", cancelled, turn_options=options,
                               inputs=inputs))
        receipt = next(e for e in events if e["kind"] == "codex.turn.settings")
        assert receipt["payload"]["status"] == (
            "not_sent" if outcome.startswith("cancelled") else outcome)
        assert calls.count("turn/start") == (outcome in {"accepted", "rejected", "unconfirmed"})
        assert loop.turn_options.selected["mode"] == (
            "plan" if outcome == "accepted" else "default")
        loop.close()
    with SessionJournal(tmp_path, "receipt") as journal:
        restored = backend(journal)
        assert restored.runtime is None
        assert restored.turn_options.selected["mode"] == (
            "plan" if outcome == "accepted" else "default")
        assert not SessionController(restored).inbox.pending


@pytest.mark.parametrize("unknown_receipt", [False, True])
def test_rejected_turn_start_preserves_receipt_uncertainty(tmp_path, monkeypatch, unknown_receipt):
    calls = []
    with SessionJournal(tmp_path, "rejected-turn-start") as journal:
        loop = backend(journal)
        from codex_thread_fixture import bind_thread
        bind_thread(loop)

        def request(method, _params, **_kwargs):
            calls.append(method)
            if method == "turn/start":
                if unknown_receipt:
                    loop.native.completed[("thread", "turn", "external")] = "needs_reconcile"
                raise RpcRejected("Model rejected the new turn")
            if method == "model/list":
                return {"data": []}
            if method == "collaborationMode/list":
                return {"data": [{"mode": "default"}, {"mode": "plan"}]}
            if method == "skills/list":
                return {"data": []}
            return {}

        monkeypatch.setattr(loop, "_ensure_runtime", lambda: None)
        loop.runtime = SimpleNamespace(rpc=SimpleNamespace(request=request), cwd="/tmp",
                                       close=lambda: None)
        events = list(loop.run("Rejected", turn_options=loop.turn_options.freeze({"mode": "plan"})))
        expected = "needs_reconcile" if unknown_receipt else "failed"
        assert events[-1]["kind"] == "task." + expected
        assert loop.state.task["status"] == expected
        assert calls.count("turn/start") == 1
        assert any(event["kind"] == "task.needs_reconcile" for event in events) == unknown_receipt
        loop.close()


def test_unconfirmed_cleanup_is_reported_without_losing_unknown_turn_result(tmp_path, monkeypatch):
    with SessionJournal(tmp_path, "unknown-turn-cleanup") as journal:
        loop = backend(journal)
        from codex_thread_fixture import bind_thread
        bind_thread(loop)

        def request(method, _params, **_kwargs):
            if method == "turn/start":
                raise RpcError("Codex app-server disconnected; no response")
            if method == "model/list":
                return {"data": []}
            if method == "collaborationMode/list":
                return {"data": [{"mode": "default"}, {"mode": "plan"}]}
            if method == "skills/list":
                return {"data": []}
            return {}

        def fail_close():
            raise RpcError("reader did not stop", code="cleanup_unconfirmed")

        monkeypatch.setattr(loop, "_ensure_runtime", lambda: None)
        loop.runtime = SimpleNamespace(rpc=SimpleNamespace(request=request), cwd="/tmp",
                                       close=fail_close)
        options = loop.turn_options.freeze({"mode": "plan"})
        events = list(loop.run("Unconfirmed", turn_options=options))
        assert events[-1]["kind"] == "task.needs_reconcile"
        assert "no response" in events[-1]["payload"]["message"]
        assert "cleanup unconfirmed" in events[-1]["payload"]["message"]
        assert loop.runtime is not None
        monkeypatch.undo()
        with pytest.raises(RpcError, match="cleanup unconfirmed"):
            loop._ensure_runtime()
        loop.runtime.close = lambda: None
        loop.close()


@pytest.mark.parametrize("preview", [False, True])
def test_input_history_retains_receipt_bytes_and_original_context(tmp_path, preview):
    path = tmp_path / "picture.png"
    path.write_bytes(PNG)
    with SessionJournal(tmp_path, "history-input") as journal:
        loop = backend(journal)
        inputs = prepare_inputs(journal, [{"type": "localImage", "path": str(path)}])
        loop._begin("Read this", CONTEXT, "original", "chat", inputs=inputs,
                    turn_options=loop.turn_options.freeze())
        sequence = next(e["sequence"] for e in journal.events() if e["kind"] == "task.started")
        loop._event("codex.turn.settings", {"status": "rejected", "options": {"mode": "plan"}})
        loop._finish("needs_reconcile", "Rejected")
        controller = SessionController(loop)
        sessions = DesktopSessions(controller)
        try:
            if preview:
                delivery = prepare_preview(
                    sessions, controller.session_id, controller.runtime_id, 7)
            else:
                delivery = prepare_replay(controller, connection(controller), 7, 40, sessions)
            result = input_detail(delivery.stream, sequence, 0)
            assert result["data"] == PNG and result["context"] == CONTEXT.record()
            assert result["settings_receipt"]["status"] == "rejected"
            assert result["settings"]["mode"] == "default"
            with pytest.raises(ValueError):
                input_detail(delivery.stream, sequence + 1)
            with pytest.raises(ValueError):
                input_detail(delivery.stream, sequence, 1)
            delivery.stream.close()
            with pytest.raises(ValueError, match="closed"):
                input_detail(delivery.stream, sequence, 0)
        finally:
            sessions.close()
            assert sessions.wait(5)
