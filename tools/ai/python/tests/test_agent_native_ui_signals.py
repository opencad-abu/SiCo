"""Exercise Qt signal arguments through compiled UI, not direct callback calls."""
from __future__ import annotations

import importlib
import importlib.machinery
import json
import os
import subprocess
from pathlib import Path

import pytest


def run_probe(runtime, work):
    import sys
    import threading
    import time
    from contextlib import ExitStack

    sys.path.insert(0, str(runtime / "python"))
    sys.path.append(str(Path(__file__).resolve().parent))
    from agent_frontend_harness import frontend
    from agent_sessions_harness import DesktopSessions
    from PyQt5.QtWidgets import QApplication

    from sico.core.contracts import BoundContext, ModelTurn, TextDelta, ToolCall, ToolResult
    from sico.service.python_backend import PythonBackend
    from sico.core.tools import Tool, ToolRegistry, no_arguments
    from sico.service.controller import SessionController
    from sico.storage.journal import SessionJournal
    from sico_ui.composer import QuickComposer
    from sico_ui.receipts import DataReceipt
    from sico_ui.window import AssistantWindow

    for name in ("window", "workspace", "composer"):
        module = importlib.import_module("sico_ui." + name)
        assert isinstance(module.__loader__, importlib.machinery.ExtensionFileLoader)
        assert runtime in Path(module.__file__).resolve().parents

    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    release = threading.Event()
    calls = []

    class Provider:
        label = "native Qt lifecycle probe"

        def __init__(self):
            self.count = 0

        def stream(self, *args):
            self.count += 1
            if self.count == 1:
                yield ModelTurn("", (ToolCall("once", "probe", {}),))
            elif self.count == 2:
                assert release.wait(3)
                yield ModelTurn("cancelled turn")
            else:
                yield TextDelta("new request finished")
                yield ModelTurn("new request finished")

    def handler(inputs, context):
        calls.append("dispatch")
        return ToolResult("needs_reconcile", "retained partial result")

    registry = ToolRegistry()
    registry.register(Tool("probe", "private lifecycle probe", {"type": "object"},
                           no_arguments, handler))
    context = BoundContext("native", "one", "bound", {"source": "simulated", "valid": True})

    def settle(window):
        deadline = time.monotonic() + 5
        while True:
            app.processEvents()
            window.commands.poll()
            window.binding.poll()
            for receipt in window.findChildren(DataReceipt):
                receipt.poll()
            delivery = window.binding.delivery
            if (not sessions.busy and not window.page.opening and not window.commands.pending
                    and window.binding.version == controller.version
                    and delivery is not None and delivery.caught_up
                    and delivery.future is None and delivery.batch is None):
                break
            assert time.monotonic() < deadline, window.status.text()
            time.sleep(0.005)
        assert not window.binding.failed

    with SessionJournal(work, "native_ui_signals") as journal, ExitStack() as cleanup:
        provider = Provider()
        loop = PythonBackend(provider, registry, journal, context)
        controller = SessionController(loop)
        sessions = DesktopSessions(controller)
        window = AssistantWindow(frontend(sessions.worker))

        def close():
            release.set()
            sessions.close()
            assert sessions.wait(5)
            window.close()
            window.deleteLater()
            app.processEvents()

        cleanup.callback(close)
        window.show()
        settle(window)
        window.input.setPlainText("trigger retained partial result")
        window.send.click()
        settle(window)
        assert loop.state.task["status"] == "needs_reconcile"
        assert calls == ["dispatch"] and not window.resume.isEnabled()
        window.abandon.click()
        settle(window)
        assert loop.state.task["status"] == "cancelled"
        assert controller.paused and calls == ["dispatch"]
        assert window.resume.isHidden() and calls == ["dispatch"]

        window.input.setPlainText("cancel this fresh turn")
        window.send.click()
        deadline = time.monotonic() + 2
        while (provider.count < 2 or not window.stop.isEnabled()) and time.monotonic() < deadline:
            app.processEvents()
            window.binding.poll()
            time.sleep(0.01)
        assert provider.count == 2 and window.stop.isEnabled()
        window.stop.click()
        deadline = time.monotonic() + 2
        while not controller._cancel.is_set() and time.monotonic() < deadline:
            app.processEvents()
            window.commands.poll()
            time.sleep(0.005)
        assert controller._cancel.is_set()
        release.set()
        settle(window)
        assert loop.state.task["status"] == "cancelled"
        assert controller.paused
        assert not window.resume_action.isVisible() and calls == ["dispatch"]

        window.input.setPlainText("finish a fresh request")
        window.send.click()
        settle(window)
        assert loop.state.task["status"] == "completed" and calls == ["dispatch"]
        next(action for action in window.view_menu.actions()
             if action.text() == "恢复默认布局").trigger()
        next(action for action in window.session_menu.actions()
             if action.text().startswith("会话历史与操作")).trigger()
        for action in window.session_menu.actions():
            if action.text() == "返回当前会话":
                action.trigger()
        window.latest.clicked.emit(False)
        window.grab().save(str(work / "native-ui-signals.png"))
        window.close()

    sent = []
    composer = QuickComposer({"destination": "private target", "source": "probe"}, sent.append)
    composer.input.setPlainText("native quick input")
    composer.submit_button.click()
    assert len(sent) == 1 and composer.pending
    composer.receive({"kind": "rejected", "message": "private rejection"})
    composer.cancel.click()
    assert composer.finished
    print(json.dumps({"passed": True, "dispatches": len(calls),
                      "checks": ["send", "cancel", "abandon", "empty_queue_controls_hidden",
                                 "menus", "latest", "quick_send", "quick_cancel"]}))


@pytest.mark.skipif(not os.environ.get("CAD_TEST_NATIVE_AI_STAGE"),
                    reason="explicit installed native AI runtime required")
def test_native_ui_buttons_and_actions_preserve_interruption_boundary(tmp_path):
    runtime = Path(os.environ["CAD_TEST_NATIVE_AI_STAGE"]).resolve(strict=True)
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONNOUSERSITE="1",
               PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run(
        ["/software/pkgs/python/3.9.13/bin/python3", "-I", "-B", str(Path(__file__).resolve()),
         str(runtime), str(tmp_path)], capture_output=True, text=True, env=env, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1])["passed"] is True


if __name__ == "__main__":
    import sys
    run_probe(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())
