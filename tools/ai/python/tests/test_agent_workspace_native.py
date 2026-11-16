"""Owned X11 workspace sizes, docking, native mouse wheel and keyboard routing."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from agent_gui_support import isolated_display, screenshot, wait_until, xdo

ROOT = Path(__file__).absolute().parents[1]
pytestmark = pytest.mark.skipif(os.environ.get("SICO_IC_TESTS") != "1", reason="Opt-in X11")


def test_native_workspace_sizes_and_floating_evidence(tmp_path):
    source = tmp_path / "workspace.py"
    source.write_text(
        f"import sys\nsys.path[:0] = [{str(ROOT)!r}, {str(ROOT / 'tests')!r}]\n"
        + """import json
from pathlib import Path
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication
from sico.core.contracts import BoundContext
from sico.service.python_backend import PythonBackend
from sico.core.tools import ToolRegistry
from sico.providers.mock import ScriptedProvider
from sico.service.controller import SessionController
from agent_frontend_harness import frontend
from agent_sessions_harness import DesktopSessions
from sico.storage.journal import SessionJournal
from sico_ui.window import AssistantWindow
app=QApplication([])
with SessionJournal(Path.cwd(), "workspace") as journal:
    context=BoundContext("i", "g", "bound", {"valid":True, "cellview":{
        "lib":"DesignLibrary", "cell":"analog_frontend", "view_name":"schematic"}})
    sessions=DesktopSessions(SessionController(
        PythonBackend(ScriptedProvider([]), ToolRegistry(), journal, context)))
    window=AssistantWindow(frontend(sessions.worker))
    lines=("第 %d 项：设计上下文与工具证据" % i for i in range(50))
    window.presentation.transcript.append("assistant", "**设计检查记录**\\n\\n" + "\\n\\n".join(lines))
    window.presentation.transcript.append("user", "请核对当前窗口，并整理检查结果。")
    window.presentation.transcript.append("assistant", "已完成只读检查。\\n\\n"
        "| 项目 | 结果 |\\n| --- | --- |\\n| 窗口绑定 | 有效 |")
    window.centers.audit.detail.setPlainText("\\n".join("交互记录 %d" % i for i in range(180)))
    window.show()
    window.renderer.flush()
    sequence=[0]
    def tick():
        if Path("command.json").is_file():
            command=json.loads(Path("command.json").read_text())
            if command["id"]>sequence[0]:
                sequence[0]=command["id"]
                window.resize(command["width"],command["height"])
                if command.get("float"):
                    window.show_panel("tools")
                    window.centers.tabs.setCurrentWidget(window.centers.audit)
                    window.panels["right"].setFloating(True)
                    window.panels["right"].setGeometry(780,40,560,650)
        view=window.centers.audit.detail
        position=view.viewport().mapToGlobal(view.viewport().rect().center())
        Path("state.part").write_text(json.dumps({"id":sequence[0],
            "width":window.width(), "height":window.height(), "input_width":window.input.width(),
            "scroll":view.verticalScrollBar().value(), "maximum":view.verticalScrollBar().maximum(),
            "point":[position.x(),position.y()], "floating":window.panels["right"].isFloating()}))
        Path("state.part").replace("state.json")
        if Path("stop").exists():
            window.close(); app.quit()
    timer=QTimer(); timer.timeout.connect(tick); timer.start(100)
    QTimer.singleShot(40000,app.quit)
    app.exec_()
"""
    )

    def state():
        path = tmp_path / "state.json"
        return json.loads(path.read_text()) if path.exists() else {}

    def command(number, width, height, floating=False):
        path = tmp_path / "command.part"
        path.write_text(
            json.dumps({"id": number, "width": width, "height": height, "float": floating})
        )
        path.replace(tmp_path / "command.json")
        return wait_until(lambda: state() if state().get("id") == number else None)

    with isolated_display(tmp_path) as env, (tmp_path / "desktop.log").open("w") as log:
        process = subprocess.Popen(
            [env["CAD_PYTHON"], "-s", str(source)],
            cwd=tmp_path,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            wait_until(lambda: state())
            window = xdo(env, "search", "--onlyvisible", "--name", "^Silicon Copilot").splitlines()[
                0
            ]
            xdo(env, "windowmove", window, "0", "0", "windowfocus", window)
            for number, width, height in ((1, 1100, 800), (2, 800, 600)):
                sample = command(number, width, height)
                assert sample["width"] <= width and sample["height"] <= height
                assert sample["input_width"] >= 260
                screenshot(env, tmp_path / f"workspace-{width}.png")
            sample = command(3, 760, 620, True)
            assert sample["floating"] and sample["maximum"] > 0
            x, y = sample["point"]
            xdo(env, "mousemove", str(x), str(y), "click", "5")
            wait_until(lambda: state()["scroll"] > sample["scroll"])
            screenshot(env, tmp_path / "floating-evidence.png")
        finally:
            (tmp_path / "stop").touch()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        assert process.returncode == 0, (tmp_path / "desktop.log").read_text()
