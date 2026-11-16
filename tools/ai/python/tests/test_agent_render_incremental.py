"""Streaming updates preserve completed Qt frames and flush terminal text promptly."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QTextBrowser

import sico_ui.transcript_render as render_module
from sico_ui.transcript import Transcript


def event(kind, **payload):
    return {"kind": kind, "payload": payload}


def settle_render(transcript, view):
    """Drain the renderer's time-sliced passes before asserting on the view."""
    for _ in range(200):
        if not transcript.dirty:
            break
        transcript.render(view, force=True)
    assert not transcript.dirty


def test_stream_throttles_preserves_prefix_and_flushes_terminal(monkeypatch):
    app = QApplication.instance() or QApplication([])
    view = QTextBrowser()
    transcript = Transcript()
    now = [10.0]
    # The streaming throttle lives in the renderer module.
    monkeypatch.setattr(render_module.time, "monotonic", lambda: now[0])
    transcript.append("user", "keep this completed bubble")
    transcript.receive(event("model.delta", text="hello"))
    assert transcript.render(view)
    prefix = view.document().rootFrame().childFrames()[0]
    revision = view.document().revision()
    transcript.receive(event("model.delta", text=" **world**"))
    now[0] += 0.05
    assert not transcript.render(view)
    assert view.document().revision() == revision
    now[0] += 0.2
    assert transcript.render(view)
    settle_render(transcript, view)
    assert view.document().rootFrame().childFrames()[0] is prefix
    assert "hello world" in view.toPlainText()
    transcript.receive(event("model.delta", text=" final"))
    transcript.receive(event("model.completed", text="hello **world** final"))
    assert transcript.render(view)
    settle_render(transcript, view)
    assert "hello world final" in view.toPlainText()
    assert view.document().rootFrame().childFrames()[0] is prefix
    assert not transcript.render(view)
    app.processEvents()


def test_incremental_replaces_activity_and_rebuilds_when_visible_prefix_is_evicted():
    app = QApplication.instance() or QApplication([])
    view = QTextBrowser()
    transcript = Transcript()
    transcript.append("user", "request")
    transcript.receive(event("model.status", label="waiting"))
    transcript.render(view)
    prefix = view.document().rootFrame().childFrames()[0]
    transcript.receive(event("model.delta", text="answer"))
    transcript.render(view, force=True)
    assert "waiting" not in view.toPlainText()
    assert view.document().rootFrame().childFrames()[0] is prefix
    assert len(view.document().rootFrame().childFrames()) == 2
    for index in range(200):
        transcript.append("user", "new message " + str(index))
    transcript.render(view, force=True)
    settle_render(transcript, view)
    assert "request" not in view.toPlainText()
    assert "new message 199" in view.toPlainText()
    assert len(view.document().rootFrame().childFrames()) == 200
    app.processEvents()


def test_character_budget_does_not_invalidate_oldest_bubble_on_every_delta():
    app = QApplication.instance() or QApplication([])
    view = QTextBrowser()
    transcript = Transcript()
    for index in range(160):
        transcript.append("assistant", str(index) + " completed text" * 100)
    transcript.receive(event("model.delta", text="answer"))
    transcript.render(view, force=True)
    prefix = view.document().rootFrame().childFrames()[0]
    for index in range(3):
        transcript.receive(event("model.delta", text=" more"))
        transcript.render(view, force=True)
        assert view.document().rootFrame().childFrames()[0] is prefix
    settle_render(transcript, view)
    assert "answer more more more" in view.toPlainText()
    app.processEvents()
