# Original test bodies retain their imports and Qt lifetime references.
# ruff: noqa: F401, F841, I001
"""agent gui contract history replay contracts."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading
import time

from agent_gui_wait import settle
from agent_window_fixture import create_window as AssistantWindow
from PyQt5.QtGui import QTextDocument
from PyQt5.QtWidgets import QApplication

from sico.adapters.virtuoso import context_tools
from sico.core.contracts import BoundContext, ModelTurn, TextDelta
from sico.service.python_backend import PythonBackend
from sico.core.tools import ToolRegistry
from sico.demo import DemoPeer
from sico.providers.mock import ContextDemoProvider
from sico.storage.journal import SessionJournal
from sico.transport.broker import ContextBroker


def test_long_history_replays_in_few_large_reads(tmp_path, monkeypatch):
    """Background reads coalesce a long journal before Qt applies bounded batches."""
    from sico.service.controller import SessionController
    from sico.service.event_display import RAW_EVENTS

    app = QApplication.instance() or QApplication([])
    pieces = [f"片段{index} " for index in range(300)]

    class Provider:
        def stream(self, *args):
            for piece in pieces:
                yield TextDelta(piece)
            yield ModelTurn("".join(pieces))

    with SessionJournal(tmp_path, "long-replay") as journal:
        loop = PythonBackend(
            Provider(), ToolRegistry(), journal,
            BoundContext("long-instance", "long-generation", "bound", {"valid": True}),
        )
        for _event in loop.run("第一轮：请检查原理图", lambda: False):
            pass
        assert len(journal.events()) > 300

        reads = []
        owner = threading.get_ident()
        from sico.service.session_updates import SessionUpdates
        original = SessionUpdates.read_updates

        def counting(self, after=0, limit=100, **options):
            assert threading.get_ident() != owner
            result = original(self, after, limit, **options)
            reads.append((self.journal.session_id, limit, len(result["events"])))
            return result

        monkeypatch.setattr(SessionUpdates, "read_updates", counting)
        window = AssistantWindow(loop)
        try:
            settle(window)
            app.processEvents()
            shown = window.display.toPlainText()
            assert "片段0 " in shown and "片段299" in shown
            # The whole history arrives in catch-up reads, not one tick each.
            mine = [(limit, count) for session, limit, count in reads
                    if session == journal.session_id]
            assert mine, "绑定没有读取事件"
            assert {limit for limit, _count in mine} == {RAW_EVENTS}
            assert mine[0][1] > 300, "整段历史应该一次抓完，而不是每个节拍 100 条"
            assert all(count < RAW_EVENTS for _limit, count in mine[1:])
        finally:
            window.close()


def test_centers_skip_status_chatter_when_snapshotting(tmp_path):
    from sico.service.workbench_data import WorkbenchService
    from sico_ui.centers import Centers

    app = QApplication.instance() or QApplication([])
    service = WorkbenchService()
    try:
        with SessionJournal(tmp_path, "centers-snapshot") as journal:
            centers = Centers(journal, service)
            for kind in ("model.delta", "model.status", "router.status", "token.usage"):
                centers._snapshot_needed = False
                centers.receive({"kind": kind})
                assert centers._snapshot_needed is False, kind
            for kind in ("tool.finished", "model.completed", "workbench.audit.prepared",
                         "task.started"):
                centers._snapshot_needed = False
                centers.receive({"kind": kind})
                assert centers._snapshot_needed is True, kind
            app.processEvents()
    finally:
        service.close()


def test_replay_coalesces_stream_chunks_and_status_chatter():
    from sico_ui.session_binding import SessionBinding

    def event(kind, index, **payload):
        return {"kind": kind, "task_id": "task", "sequence": index, "payload": payload}

    events = [
        event("model.status", 1, phase="thinking", label="等待 LLM 响应"),
        event("model.status", 2, phase="thinking", label="整理工具结果"),
        event("model.delta", 3, text="半"),
        event("model.delta", 4, text="句"),
        event("model.completed", 5, text="半句。完整回答。"),
        event("tool.started", 6, id="call-1", name="lookup"),
        event("model.status", 7, phase="tool_call", tool="lookup"),
        # A second turn in the same batch keeps its own stream chunks.
        event("model.delta", 8, text="后续"),
    ]
    kept = SessionBinding.coalesce(events)
    # Deltas before the completed answer go, older statuses go, the rest stays.
    assert [row["sequence"] for row in kept] == [5, 6, 7, 8]
    # A batch that stops before the completed event keeps every stream chunk.
    assert [row["sequence"] for row in SessionBinding.coalesce(events[:4])] == [2, 3, 4]


def test_long_history_replays_from_the_newest_turns(tmp_path, monkeypatch):
    from sico_ui.session_binding import SessionBinding

    app = QApplication.instance() or QApplication([])

    class Provider:
        def __init__(self):
            self.turn = 0

        def stream(self, *args):
            text = f"第 {self.turn} 轮回答"
            self.turn += 1
            yield TextDelta(text)
            yield ModelTurn(text)

    with SessionJournal(tmp_path, "windowed-replay") as journal:
        context = BoundContext("window-instance", "window-generation", "bound", {"valid": True})
        loop = PythonBackend(Provider(), ToolRegistry(), journal, context)
        for turn in range(8):
            for _event in loop.run(f"第 {turn} 轮：请检查原理图", lambda: False):
                pass
        assert journal.replay_start(4) > 1
        monkeypatch.setattr(SessionBinding, "REPLAY_MESSAGES", 6)
        window = AssistantWindow(loop)
        try:
            settle(window)
            shown = window.display.toPlainText()
            assert "第 7 轮回答" in shown
            assert "第 0 轮回答" not in shown
            assert window.presentation.transcript.messages
        finally:
            window.close()


def test_centers_defer_the_first_snapshot_until_the_chat_lands(tmp_path):
    from sico.service.workbench_data import WorkbenchService
    from sico_ui.centers import Centers

    app = QApplication.instance() or QApplication([])
    service = WorkbenchService()
    try:
        with SessionJournal(tmp_path, "centers-defer") as journal:
            centers = Centers(journal, service)
            deadline = time.monotonic() + 5
            while centers._snapshot_receipt.pending and time.monotonic() < deadline:
                centers._snapshot_receipt.poll()
                app.processEvents()
                time.sleep(0.005)
            assert not centers._snapshot_receipt.pending
            centers._snapshot_needed = True
            centers.defer_snapshot()
            centers.request_snapshot()
            assert centers._snapshot_needed is True
            assert not centers._snapshot_receipt.pending
            centers.release_snapshot()
            centers.request_snapshot()
            assert centers._snapshot_receipt.pending
            app.processEvents()
    finally:
        service.close()


def test_worker_stream_history_reopen_and_idle_render(tmp_path, monkeypatch):
    from sico import PRODUCT_NAME

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(app, "quit", lambda: None)  # Reopening history keeps the test app alive.
    with SessionJournal(tmp_path, "gui") as journal, ContextBroker() as broker:
        peer = DemoPeer(broker)
        try:
            loop = PythonBackend(
                ContextDemoProvider(), context_tools(broker), journal, peer.context)
            loop._event("session.created", {"entry": "test"})
            window = AssistantWindow(loop)
            settle(window)
            # The desktop host registers its logical session before queued inputs.
            window.sessions.broker = broker
            window.actions.run_command("attach_targets")
            assert window.windowTitle() == PRODUCT_NAME
            window.show()
            window.input.setPlainText("请读取绑定窗口")
            window.actions.submit()
            settle(window)
            deadline = time.monotonic() + 5
            while window.controller.busy and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.01)
            assert not window.controller.busy
            window.binding.poll()
            assert loop.state.task["status"] == "completed"
            assert "请读取绑定窗口" in window.display.toPlainText()
            assert "工具返回数据" in window.display.toPlainText()
            assert "demo / amp / schematic" in window.display.toPlainText()
            assert '"cellview"' not in window.display.toPlainText()
            revision = window.display.document().revision()
            window.renderer.flush()
            window.renderer.flush()
            assert window.display.document().revision() == revision
            window.grab().save(str(tmp_path / "assistant.png"))
            window.close()
            reopened = AssistantWindow(loop)
            settle(reopened)
            assert "请读取绑定窗口" in reopened.display.toPlainText()
            assert "工具返回数据" in reopened.display.toPlainText()
            assert reopened.display.loadResource(QTextDocument.ImageResource, None) is None
            reopened.close()
        finally:
            peer.close()


def test_hide_active_task_then_restore_completed_history(tmp_path):
    app = QApplication.instance() or QApplication([])
    completed = threading.Event()

    class Provider:
        def stream(self, *args):
            yield TextDelta("before")
            assert completed.wait(3)
            yield TextDelta(" after")
            yield ModelTurn("before after")

    with SessionJournal(tmp_path, "persistent") as journal, ContextBroker() as broker:
        peer = DemoPeer(broker)
        try:
            loop = PythonBackend(Provider(), context_tools(broker), journal, peer.context)
            notices = []
            loop._event("session.created", {"entry": "test"})
            window = AssistantWindow(loop, persistent=True,
                                     notice=lambda kind, **_payload: notices.append(kind))
            settle(window)
            window.restore()
            window.input.setPlainText("continue while hidden")
            window.actions.submit()
            window.close()
            assert not window.isVisible() and window.sessions.busy
            completed.set()
            settle(window)
            deadline = time.monotonic() + 4
            while window.controller.busy and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.01)
            assert not window.controller.busy
            window.binding.poll()
            assert loop.state.task["status"] == "completed"
            window.restore()
            assert window.isVisible()
            # Worker completion precedes queued event delivery and incremental paint.
            settle(window)
            assert "before after" in window.display.toPlainText()
            assert notices == ["shown", "hidden", "shown"]
            window.request_quit()
        finally:
            completed.set()
            peer.close()
