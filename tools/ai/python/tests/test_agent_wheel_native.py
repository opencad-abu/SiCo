"""Actual X11 mouse input on an owned desktop; no user's windows are touched."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from agent_gui_support import isolated_display, screenshot, wait_until, xdo

PYTHON_ROOT = Path(__file__).absolute().parents[1]
pytestmark = pytest.mark.skipif(
    os.environ.get("SICO_IC_TESTS") != "1", reason="Opt-in native X11 wheel test"
)


def test_native_mouse_wheel_during_stream_and_return_to_latest(tmp_path):
    source = tmp_path / "wheel_desktop.py"
    source.write_text(
        f"import sys\nsys.path[:0] = {[*sys.path, str(PYTHON_ROOT / 'tests')]!r}\n"
        + """import json
from pathlib import Path
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication
from sico.core.contracts import BoundContext
from sico.service.python_backend import PythonBackend
from sico.core.tools import ToolRegistry
from sico.providers.mock import ContextDemoProvider
from sico.service.controller import SessionController
from agent_frontend_harness import frontend
from agent_sessions_harness import DesktopSessions
from sico.storage.journal import SessionJournal
from sico_ui.wheel import prepare_wheel_environment
from sico_ui.window import AssistantWindow

prepare_wheel_environment()
app = QApplication([])
with SessionJournal(Path.cwd(), "native-wheel") as journal:
    loop = PythonBackend(ContextDemoProvider(), ToolRegistry(), journal,
                     BoundContext("i", "g", "bound", {}))
    sessions = DesktopSessions(SessionController(loop))
    window = AssistantWindow(frontend(sessions.worker))
    window.presentation.transcript.append("assistant", "\\n\\n".join(
        "**历史消息 %d**：实际鼠标滚轮测试" % i for i in range(180)))
    window.show()
    window.renderer.flush()
    ticks = [0]
    def sample():
        ticks[0] += 1
        window.renderer.receive({"kind": "model.delta", "payload": {"text": "\\n\\n实时输出"}})
        window.renderer.flush()
        bar = window.display.verticalScrollBar()
        button = window.latest.mapTo(window, window.latest.rect().center())
        Path("state.part").write_text(json.dumps({
            "value": bar.value(), "maximum": bar.maximum(), "ticks": ticks[0],
            "latest": [button.x(), button.y()], "enabled": window.latest.isEnabled()}))
        Path("state.part").replace("state.json")
        if Path("stop").exists():
            window.close()
            app.quit()
    timer = QTimer()
    timer.timeout.connect(sample)
    timer.start(100)
    QTimer.singleShot(30000, app.quit)
    app.exec_()
"""
    )
    state_path = tmp_path / "state.json"

    def state():
        return json.loads(state_path.read_text()) if state_path.exists() else {}

    with isolated_display(tmp_path) as env, (tmp_path / "desktop.log").open("w") as log:
        env.update(QT_XCB_NO_XI2="1", QT_XCB_NO_XI2_MOUSE="1")
        process = subprocess.Popen(
            [env["CAD_PYTHON"], "-s", str(source)],
            cwd=tmp_path,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            wait_until(lambda: state().get("ticks", 0) > 2)
            window = xdo(env, "search", "--onlyvisible", "--name", "^Silicon Copilot").splitlines()[
                0
            ]
            xdo(env, "windowmove", window, "20", "20")
            xdo(env, "windowfocus", window)
            xdo(env, "mousemove", "--window", window, "250", "240", "click", "4")
            previous = wait_until(lambda: state() if state().get("enabled") else None)
            later = wait_until(
                lambda: state() if state().get("ticks", 0) > previous["ticks"] + 4 else None
            )
            assert later["value"] == previous["value"] < later["maximum"]
            screenshot(env, tmp_path / "scrolled-history.png")
            xdo(env, "click", "5")
            wait_until(lambda: state()["value"] > previous["value"])
            x, y = state()["latest"]
            xdo(env, "mousemove", "--window", window, str(x), str(y), "click", "1")
            wait_until(lambda: not state()["enabled"])
            screenshot(env, tmp_path / "following-latest.png")
        finally:
            (tmp_path / "stop").touch()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        assert process.returncode == 0, (tmp_path / "desktop.log").read_text()
