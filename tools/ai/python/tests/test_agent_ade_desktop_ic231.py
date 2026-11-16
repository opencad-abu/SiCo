"""ADE menu launch -> actual PyQt input -> Agent tool -> real SKILL -> evidence view."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from agent_gui_support import isolated_display, quick_form, screenshot, wait_until, xdo

CAD = Path(__file__).absolute().parents[3]
IC = Path("/software/eda/cadence/installs/IC23.10.130/tools/dfII/bin/virtuoso")
pytestmark = pytest.mark.skipif(
    os.environ.get("SICO_IC_TESTS") != "1", reason="Opt-in real desktop test"
)


@pytest.mark.parametrize("entry", ["results", "history"])
def test_ade_desktop_task_and_hidden_session_restore(tmp_path, entry):
    stop, restore = tmp_path / "stop", tmp_path / "restore"
    source = tmp_path / "desktop.il"
    source.write_text(
        "\n".join(
            [
                f"load({json.dumps(str(CAD / 'sico/skill++/SICO.ils'))})",
                f'ddCreateLib("AgentDesktop" {json.dumps(str(tmp_path / "library"))})',
                'cv=dbOpenCellViewByType("AgentDesktop" "tb" "schematic" "schematic" "w")',
                "dbSave(cv) dbClose(cv)",
                'session=maeOpenSetup("AgentDesktop" "tb" "maestro" ?application "Assembler")',
                'foreach(name list("nominal" "other") '
                'maeCreateTest(name ?lib "AgentDesktop" ?cell "tb"',
                '  ?view "schematic" ?simulator "spectre" ?session session))',
                'maeAddOutput("gain" "nominal" ?expr "1" ?session session)',
                'maeSetVar("VDD" "1.2" ?session session)',
                "maeSaveSetup(?session session) maeCloseSession(?session session)",
                'adeWin=geOpen(?lib "AgentDesktop" ?cell "tb" ?view "maestro")',
                "procedure(agentDesktopStart()",
                "  session=axlGetWindowSession(adeWin)",
                (
                    '  items=axlGetCustomOutputResultMenu(?axlSession session ?testName "nominal")'
                    if entry == "results"
                    else "  history=axlPutHistoryEntry(axlGetMainSetupDB(session) "
                    '"AgentSynthetic.1") '
                    "items=axlGetDataViewHistoryUserMenu(session history)"
                ),
                "  menuCommand=cadr(car(last(items)))",
                (
                    "  hiSetCurrentWindow(hiGetCIWindow()) evalstring(menuCommand)"
                    if entry == "results"
                    else '  printf("RIGHTCLICK_READY\\n")'
                ),
                '  probeRestored=nil probeTicks=0 hiRegTimer("agentDesktopPoll()" 2))',
                "procedure(agentDesktopPoll()",
                "  probeTicks=probeTicks+1",
                f"  when(and(!probeRestored isFile({json.dumps(str(restore))}))",
                "    probeRestored=t sicoShow(adeWin))",
                f"  if(or(isFile({json.dumps(str(stop))}) probeTicks>600) then",
                "    foreach(cid sicoBindings "
                "when(sicoBindings[cid] sicoStopDesktop(cid)))",
                "    maeSaveSetup(?session session) "
                "maeCloseSession(?session session ?forceClose t)",
                '    hiRegTimer("exit()" 20)',
                '  else hiRegTimer("agentDesktopPoll()" 2)))',
                'hiRegTimer("agentDesktopStart()" 30)',
            ]
        )
        + "\n"
    )

    def events():
        paths = list((tmp_path / ".cad/ai/agent/sessions").glob("*/events.jsonl"))
        if not paths:
            return []
        raw = paths[0].read_text()
        return [json.loads(line) for line in raw.splitlines(keepends=True) if line.endswith("\n")]

    with isolated_display(tmp_path) as env, (tmp_path / "stdout.log").open("w") as out:
        process = subprocess.Popen(
            [str(IC), "-nocdsinit", "-log", str(tmp_path / "CDS.log"), "-restore", str(source)],
            cwd=tmp_path,
            env=env,
            stdout=out,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )
        try:
            if entry == "history":
                wait_until(
                    lambda: (
                        (tmp_path / "CDS.log").is_file()
                        and "RIGHTCLICK_READY" in (tmp_path / "CDS.log").read_text()
                    ),
                    40,
                )
                ade = xdo(
                    env, "search", "--onlyvisible", "--name", "ADE Assembler Editing:"
                ).splitlines()[0]
                xdo(env, "windowmove", ade, "0", "0")
                xdo(env, "windowfocus", ade)
                xdo(env, "mousemove", "--window", ade, "70", "452", "click", "1")
                xdo(env, "mousemove", "--window", ade, "120", "128", "click", "3")
                screenshot(env, tmp_path / "history-rightclick.png")
                xdo(env, "mousemove", "220", "667", "click", "1")
            quick_form(env, "Read ADE setup")
            window = wait_until(
                lambda: xdo(
                    env, "search", "--onlyvisible", "--name", "^Silicon Copilot", check=False
                ),
                40,
            ).splitlines()[0]
            xdo(env, "windowmove", window, "20", "20")
            xdo(env, "windowfocus", window)

            def send(message):
                xdo(env, "key", "--clearmodifiers", "ctrl+l")
                xdo(env, "key", "--clearmodifiers", "ctrl+a")
                xdo(env, "type", "--clearmodifiers", message)
                xdo(env, "key", "--clearmodifiers", "Return")

            wait_until(lambda: sum(e["kind"] == "task.completed" for e in events()) == 2)
            results = [e["payload"] for e in events() if e["kind"] == "tool.finished"]
            setup = results[-1]["result"]["data"]
            assert results[-1]["name"] == "read_ade_setup"
            assert setup["session_test_count"] == 2
            assert setup["bound_test"] == ("nominal" if entry == "results" else "")
            assert setup["bound_history"] == ("AgentSynthetic.1" if entry == "history" else "")
            assert len(setup["tests"]) == (1 if entry == "results" else 2)
            assert setup["tests"][0]["outputs"][0]["name"] == "gain"
            xdo(env, "key", "--clearmodifiers", "ctrl+shift+t")
            screenshot(env, tmp_path / "ade-setup-desktop.png")
            xdo(env, "key", "--clearmodifiers", "ctrl+w")
            wait_until(
                lambda: (
                    not xdo(
                        env, "search", "--onlyvisible", "--name", "^Silicon Copilot", check=False
                    )
                )
            )
            restore.touch()
            restored = wait_until(
                lambda: xdo(
                    env, "search", "--onlyvisible", "--name", "^Silicon Copilot", check=False
                )
            )
            assert window in restored.splitlines()
            xdo(env, "windowfocus", window)
            send("Read History")
            wait_until(lambda: sum(e["kind"] == "task.completed" for e in events()) == 3)
            result = [e["payload"] for e in events() if e["kind"] == "tool.finished"][-1]
            assert result["name"] == "read_ade_history"
            histories = result["result"]["data"]["histories"]
            assert [item["name"] for item in histories] == (
                ["AgentSynthetic.1"] if entry == "history" else []
            )
            screenshot(env, tmp_path / "ade-history-desktop.png")
            assert len(list((tmp_path / ".cad/ai/agent/sessions").iterdir())) == 1
        finally:
            stop.touch()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        log = (tmp_path / "CDS.log").read_text()
        assert "*Error*" not in log and "Fatal Python error" not in log, log
