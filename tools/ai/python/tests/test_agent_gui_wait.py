"""The Qt test barrier includes the final snapshot and its visible output."""

from PyQt5.QtCore import QTimer

from agent_gui_wait import settle, wait_until
from agent_worker_fixtures import window as window
from sico.core.contracts import ModelTurn, TextDelta
from sico.providers.mock import ScriptedProvider


def test_settle_waits_for_final_published_snapshot(window, monkeypatch):
    controller = window.controller
    window.binding.timer.stop()
    # Drain a poll that settle may have requested before this task begins.
    def drained():
        window.binding.poll()
        return window.binding.delivery.future is None
    wait_until(drained)
    controller.loop.provider = ScriptedProvider([
        [TextDelta("visible completion"), ModelTurn("visible completion")],
    ])
    controller.submit("render after publication")
    assert controller.wait(5)
    assert controller.loop.state.task["status"] == "completed"
    assert window.binding.version < controller.version
    poll = window.binding.poll
    released = []
    monkeypatch.setattr(window.binding, "poll", lambda: poll() if released else None)
    QTimer.singleShot(25, lambda: released.append(True))

    settle(window)

    assert released, "The test barrier returned before the final publication was available"
    assert window.binding.version == controller.version
    assert "visible completion" in window.display.toPlainText()
