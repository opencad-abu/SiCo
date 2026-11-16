"""Native Ask routing by library/cell, with each task's captured view preserved."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from agent_gui_support import isolated_display, quick_form, screenshot, wait_until, xdo

CAD = Path(__file__).absolute().parents[3]
IC = Path("/software/eda/cadence/installs/IC23.10.130/tools/dfII/bin/virtuoso")
pytestmark = pytest.mark.skipif(os.environ.get("SICO_IC_TESTS") != "1", reason="Opt-in IC231")


def test_same_cell_views_reuse_session_and_unrelated_cells_open_another(tmp_path):
    source = tmp_path / "startup.il"
    source.write_text(f'''
load({json.dumps(str(CAD / "sico/skill++/SICO.ils"))})
ddCreateLib("AgentQuick" {json.dumps(str(tmp_path / "library"))})
foreach(name list("first" "second")
 cv=dbOpenCellViewByType("AgentQuick" name "schematic" "schematic" "w")
 dbSave(cv) dbClose(cv))
cv=dbOpenCellViewByType("AgentQuick" "first" "schematic_v2" "schematic" "w")
dbSave(cv) dbClose(cv)
firstWin=geOpen(?lib "AgentQuick" ?cell "first" ?view "schematic" ?mode "r")
variantWin=geOpen(?lib "AgentQuick" ?cell "first" ?view "schematic_v2" ?mode "r")
secondWin=geOpen(?lib "AgentQuick" ?cell "second" ?view "schematic" ?mode "r")
procedure(openQuick(win)
 let((binding)
  binding=makeTable("quickTest" nil) binding['window]=win binding['entry]="schematic_rmb"
  sicoContextJson(binding) evalstring(sicoMenuCommand(binding))))
probeNext=1
procedure(quickPoll() let((file)
 file=sprintf(nil "{tmp_path}/command_%d.il" probeNext)
 when(isFile(file) probeNext=probeNext+1 load(file))
 if(isFile("{tmp_path}/stop") then
  foreach(cid sicoBindings when(sicoBindings[cid] sicoStopDesktop(cid)))
  hiRegTimer("exit()" 20)
 else hiRegTimer("quickPoll()" 2))))
hiRegTimer("quickPoll()" 2)
printf("QUICK_READY\\n")
''')
    counter = 0

    def log():
        path = tmp_path / "CDS.log"
        return path.read_text() if path.exists() else ""

    def command(code):
        nonlocal counter
        counter += 1
        path = tmp_path / f"command_{counter}.il"
        path.with_suffix(".part").write_text(code + f'\nprintf("STEP{counter}\\n")\n')
        path.with_suffix(".part").replace(path)
        wait_until(lambda: f"\\o STEP{counter}\n" in log())

    def sessions():
        return sorted((tmp_path / ".cad/ai/agent/sessions").glob("*/events.jsonl"))

    def events(path):
        rows = [
            json.loads(line)
            for line in path.read_text().splitlines(keepends=True)
            if line.endswith("\n")
        ]
        initial = {
            e["task_id"]
            for e in rows
            if e["kind"] == "task.started" and e["payload"].get("origin") == "startup"
        }
        assert len(initial) <= 1
        return [e for e in rows if e["task_id"] not in initial]

    def completed(path, count):
        return sum(e["kind"] == "task.completed" for e in events(path)) == count

    with isolated_display(tmp_path) as env, (tmp_path / "stdout.log").open("w") as output:
        process = subprocess.Popen(
            [str(IC), "-nocdsinit", "-log", str(tmp_path / "CDS.log"), "-restore", str(source)],
            cwd=tmp_path,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:
            wait_until(lambda: "\\o QUICK_READY\n" in log(), 40)
            command("openQuick(firstWin)")
            command(
                "unless(sicoQuickWindows[firstWin]['source]=="
                '"AgentQuick / first / schematic" error("Wrong source label"))'
            )
            screenshot(env, tmp_path / "first-quick-input.png")
            quick_form(env, cancel=True)
            assert sessions() == []
            command("openQuick(firstWin)")
            quick_form(env, '请读取当前设计上下文。\n检查引号"和路径 /tmp/测试。')
            first_session = wait_until(lambda: sessions())[0]
            wait_until(lambda: completed(first_session, 1))
            assert next(
                e["payload"]["text"] for e in events(first_session) if e["kind"] == "task.started"
            ) == ('请读取当前设计上下文。\n检查引号"和路径 /tmp/测试。')
            command("openQuick(variantWin) hiSetCurrentWindow(secondWin)")
            quick_form(env, "Read schematic variant")
            wait_until(lambda: completed(first_session, 2))
            assert len(sessions()) == 1
            command("openQuick(secondWin) hiSetCurrentWindow(firstWin)")
            quick_form(env, "Read second source")
            wait_until(lambda: len(sessions()) == 2)
            second_session = next(path for path in sessions() if path != first_session)
            wait_until(lambda: completed(second_session, 1))
            command(
                "unless(sicoWindowSessions[firstWin]==sicoWindowSessions[secondWin] "
                'error("Ask unexpectedly opened another desktop process"))'
            )
            command("openQuick(firstWin)")
            quick_form(env, "Read first again")
            wait_until(lambda: completed(first_session, 3))
            targets = [
                e["payload"]["context"]["snapshot"]["cellview"]
                for e in events(first_session)
                if e["kind"] == "task.started"
            ]
            assert [(row["cell"], row["view"]) for row in targets] == [
                ("first", "schematic"), ("first", "schematic_v2"), ("first", "schematic"),
            ]
            command("openQuick(secondWin)")
            quick_form(env, "Read second source again")
            wait_until(lambda: completed(second_session, 2))
            assert len(sessions()) == 2
            # Shared get_entry_context refresh and legacy/model get_context
            # may both occur. Every observed result must retain that task's source.
            for path in sessions():
                for start in (e for e in events(path) if e["kind"] == "task.started"):
                    expected = start["payload"]["context"]["snapshot"]["cellview"]
                    reads = [
                        e["payload"]["result"]["data"] for e in events(path)
                        if e["kind"] == "tool.finished" and e["task_id"] == start["task_id"]
                    ]
                    assert reads
                    assert all(data.get("source", data)["cellview"] == expected for data in reads)
            screenshot(env, tmp_path / "two-design-sessions.png")
            wait_until(
                lambda: (
                    not xdo(
                        env,
                        "search",
                        "--onlyvisible",
                        "--name",
                        "^Ask Silicon Copilot$",
                        check=False,
                    )
                )
            )
            # A session ending while its composer is open must retain the text;
            # it must not silently start an additional conversation on submit.
            command("openQuick(secondWin)")
            quick_form(env, "Keep my request when the session ends", submit=False)
            command("""
form=sicoQuickWindows[secondWin]
sicoStopDesktop(form['session])
""")
            quick_form(env)
            command('when(form[\'pending] error("Submission accepted after session ended"))')
            assert len(sessions()) == 2
            quick_form(env, cancel=True)
        finally:
            (tmp_path / "stop").touch()
            try:
                process.wait(timeout=12)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        assert "*Error*" not in log(), log()
        assert "Fatal Python error" not in log(), log()
        assert "Traceback" not in log(), log()
