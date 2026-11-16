"""Real proj1 PDK reads must not end the Qt session that owns the host calls.

The model is scripted; tools, TCP bridge, SKILL and PDK observations are real.
Only private runtime evidence is written. No design is created or changed.
Run after loading proj1, with CAD_AI_RUN_ROUTER_TESTS=1.
"""

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from agent_gui_wait import settle, wait_until
from agent_sessions_harness import DesktopSessions
from agent_window_fixture import create_window as AssistantWindow
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication

from sico.adapters.virtuoso import context_tools
from sico.core.contracts import BoundContext, ModelTurn, TextDelta, ToolCall
from sico.service.python_backend import PythonBackend
from sico.providers.mock import ScriptedProvider
from sico.service.controller import SessionController
from sico.storage.journal import SessionJournal
from sico.transport.bridge_client import BridgeClient
from sico.transport.bridge_identity import discovery_path

CAD = Path(__file__).absolute().parents[3]
IC = Path("/software/eda/cadence/installs/IC23.10.130/tools/dfII/bin/virtuoso")
PROJECT = Path("/workarea/projs/proj1/xh")


@pytest.mark.skipif(os.environ.get("CAD_AI_RUN_ROUTER_TESTS") != "1" or not IC.is_file(),
                    reason="licensed PDK acceptance requires CAD_AI_RUN_ROUTER_TESTS=1")
def test_real_pdk_preparation_keeps_desktop_alive(tmp_path):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    identity, done = tmp_path / "identity.json", tmp_path / "done"
    startup = tmp_path / "startup.il"
    startup.write_text("\n".join([
        f'load({json.dumps(str(CAD / "sico/skill++/SICO.ils"))})',
        'pdkSource=makeTable("pdkSource" nil) pdkSource[\'entry]="menu"',
        'pdkBridge=sicoEnsureBridge(pdkSource)',
        f'pdkIdentity=outfile({json.dumps(str(identity))})',
        'fprintf(pdkIdentity "{\\"instance_id\\":\\"%s\\",\\"generation\\":\\"%s\\",\\"snapshot\\":%s}"'
        ' sicoBindings[pdkBridge][\'instanceId] sicoBindings[pdkBridge][\'generation]'
        ' sicoContextJson(sicoBindings[pdkBridge])) close(pdkIdentity)',
        'procedure(pdkExit() sicoDisconnect(pdkBridge) exit())',
        f'procedure(pdkPoll() if(isFile({json.dumps(str(done))}) then pdkExit()'
        ' else hiRegTimer("pdkPoll()" 1))) hiRegTimer("pdkPoll()" 1)',
    ]) + "\n")
    env = {key: value for key, value in os.environ.items()
           if key not in {"LD_LIBRARY_PATH", "LD_PRELOAD", "PYTHONHOME", "PYTHONPATH"}
           and not key.startswith("QT_")}
    env.update(SICO_HOME=str(CAD.parent), CAD_PYTHON="/software/pkgs/python/3.9.13/bin/python3",
               PROJ_HOME=str(PROJECT.parent), CAD_TEMP_DIR=str(tmp_path / ".cad/ai"))
    with (tmp_path / "stdout.log").open("w") as output:
        process = subprocess.Popen(
            [str(IC), "-nocdsinit", "-nographE", "-cdslib", str(PROJECT / "cds.lib"),
             "-restore", str(startup), "-log", str(tmp_path / "CDS.log")],
            cwd=tmp_path, env=env, stdin=subprocess.DEVNULL, stdout=output,
            stderr=subprocess.STDOUT,
        )
    try:
        deadline = time.monotonic() + 45
        while not identity.exists():
            assert process.poll() is None and time.monotonic() < deadline, (
                tmp_path / "stdout.log").read_text()[-4000:]
            time.sleep(0.1)
        record = json.loads(identity.read_text())
        context = BoundContext(record["instance_id"], record["generation"], "bound",
                               record["snapshot"])
        with BridgeClient.discover(tmp_path, context) as client, \
                SessionJournal(tmp_path, "pdk-live") as journal:
            provider = ScriptedProvider([
                [ModelTurn("", (ToolCall("entry", "get_entry_context", {}),))],
                [ModelTurn("", (ToolCall("libs", "list_libraries", {"max_items": 40}),))],
                [ModelTurn("", (ToolCall("pdk", "get_pdk_preparation", {}),))],
                [ModelTurn("", (ToolCall("project", "get_project_context", {}),))],
                [TextDelta("PDK 查询完成，请提供 LDO 指标。"), ModelTurn("PDK 查询完成，请提供 LDO 指标。")],
                [TextDelta("可以继续设计"), ModelTurn("可以继续设计")],
            ])
            loop = PythonBackend(provider, context_tools(client, journal), journal, context)
            loop._event("session.created", {})
            controller = SessionController(loop)
            controller.bindings.attach_targets(client)
            sessions = DesktopSessions(controller, broker=client)
            window = AssistantWindow(controller, sessions=sessions)
            ticks = []
            timer = QTimer(window)
            timer.timeout.connect(lambda: ticks.append(True))
            timer.start(10)
            window.show()
            try:
                settle(window)
                window.input.setPlainText("我想设计一个LDO")
                window.actions.submit()
                settle(window, timeout=90)
                wait_until(lambda: any(e["kind"] == "binding.operation_started"
                                       for e in journal.events()))
                window.binding.poll()
                events = journal.events()
                results = {e["payload"]["name"]: e["payload"]["result"] for e in events
                           if e["kind"] == "tool.finished"}
                (tmp_path / "tool-results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
                assert results["get_entry_context"]["status"] == "ok"
                assert results["get_entry_context"]["data"]["captured"] is False
                assert results["list_libraries"]["status"] == "ok"
                assert "smic28hkmg" in json.dumps(results["list_libraries"])
                assert results["get_pdk_preparation"]["status"] == "ok", results["get_pdk_preparation"]
                assert results["get_project_context"]["status"] == "ok"
                assert loop.state.task["status"] == "completed"
                assert not window.binding.failed and not controller.closing
                assert "pdk-live" in client.bridge_snapshot()["sessions"]
                assert len(ticks) >= 3 and window.input.isEnabled() and window.send.isEnabled()
                window.input.setPlainText("输入 3.3V，输出 1.8V")
                window.actions.submit()
                settle(window)
                assert "可以继续设计" in window.display.toPlainText()
                assert not any(e["kind"] in {"task.cancelled", "task.needs_reconcile"}
                               for e in journal.events())
                assert "bridge.session_release" not in discovery_path(
                    tmp_path, context).with_suffix(".router.jsonl").read_text()
                assert window.grab().save(str(tmp_path / "pdk-desktop.png"))
                (tmp_path / "acceptance.json").write_text(json.dumps(dict(
                    passed=True, model="scripted", host=context.record(),
                    task_status=loop.state.task["status"], ui_ticks=len(ticks),
                    events=len(journal.events()), session_registered=True,
                ), ensure_ascii=False, indent=2))
            finally:
                timer.stop()
                sessions.close()
                assert sessions.wait(5)
                window.close()
                window.deleteLater()
                app.processEvents()
    finally:
        done.touch()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)
