"""Real IC231 menu/IPC/desktop lifecycle with an isolated empty schematic library."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

CAD = Path(__file__).absolute().parents[3]
IC = Path("/software/eda/cadence/installs/IC23.10.130/tools/dfII/bin/virtuoso")
pytestmark = pytest.mark.skipif(
    os.environ.get("SICO_IC_TESTS") != "1", reason="Opt-in real IC231 menu test"
)


def test_menu_launch_restore_and_bound_window(tmp_path):
    source = tmp_path / "menu_probe.il"
    source.write_text(
        "\n".join(
            [
                f'procedure(cadPath(name) strcat({json.dumps(str(CAD))} "/" name))',
                f"load({json.dumps(str(CAD / 'utility/menu.il'))})",
                'unless(and(isCallable(\'sicoAskIcon) hiIsIcon(sicoAskIcon()))',
            '  error("Silicon Copilot menu icon is unavailable"))',
                'ciwBinding=makeTable("ciwBinding" nil)',
                "ciwBinding['window]=hiGetCIWindow()",
                "sicoContextJson(ciwBinding)",
                "unless(and(ciwBinding['valid] ciwBinding['cellJson]==\"null\")",
                '  error("CIW context invalid"))',
                f'probeLib=ddCreateLib("AgentMenuProbe" {json.dumps(str(tmp_path / "library"))})',
                'foreach(name list("first" "second")',
                '  cv=dbOpenCellViewByType("AgentMenuProbe" name "schematic" "schematic" "w")',
                "  dbSave(cv) dbClose(cv))",
                'firstWin=geOpen(?lib "AgentMenuProbe" ?cell "first" ?view "schematic" ?mode "r")',
                'secondWin=geOpen(?lib "AgentMenuProbe" ?cell "second" '
                '?view "schematic" ?mode "r")',
                "hiSetCurrentWindow(firstWin)",
                "menuSpec=car(cadddr(witMenuSchematic))",
                'unless(car(menuSpec)=="aiAssistantAgent" error("Agent menu is absent"))',
            'unless(cadr(menuSpec)=="Silicon Copilot" error("Stale product name"))',
                'setShellEnvVar("QT_XCB_NO_XI2" "1")',
                'setShellEnvVar("QT_XCB_NO_XI2_MOUSE" "1")',
                "evalstring(caddr(menuSpec))",
                "probeCid=nil foreach(item sicoBindings "
                "when(sicoBindings[item] probeCid=item))",
                'unless(probeCid error("Menu did not start a Relay"))',
                "hiSetCurrentWindow(secondWin)",
                "probeCount=0 probePhase=0",
                "procedure(agentMenuProbeTick()",
                "  probeCount=probeCount+1",
                '  when(probeCount>200 printf("AGENT_MENU_TIMEOUT\\n") exit())',
                "  case(probePhase",
                "    (0 when(sicoBindings[probeCid]['shownCount]",
                '      printf("AGENT_MENU_CONTEXT %s\\n" '
                "sicoContextJson(sicoBindings[probeCid]))",
                '      unless(sicoShow(firstWin)==probeCid error("Duplicate desktop"))',
                "      probePhase=1))",
                "    (1 when(sicoBindings[probeCid]['shownCount]>=2",
                "      hiCloseWindow(firstWin)",
                '      printf("AGENT_CLOSED_CONTEXT %s\\n" '
                "sicoContextJson(sicoBindings[probeCid]))",
                "      sicoStopDesktop(probeCid) probePhase=2))",
                '    (2 unless(sicoBindings[probeCid] printf("AGENT_MENU_PASS\\n") exit())))',
                '  hiRegTimer("agentMenuProbeTick()" 2))',
                'hiRegTimer("agentMenuProbeTick()" 2)',
            ]
        )
        + "\n"
    )
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("QT_", "SICO_"))
        and k
        not in {
            "LD_LIBRARY_PATH",
            "LD_PRELOAD",
            "PYTHONPATH",
            "PYTHONHOME",
            "SICO_PROVIDER_CONFIG",
            "SICO_ROOT",
        }
    }
    env["CAD_PYTHON"] = "/software/pkgs/python/3.9.13/bin/python3"
    env["PWD"] = str(tmp_path)
    with (tmp_path / "stdout.log").open("w") as output:
        process = subprocess.Popen(
            [str(IC), "-nocdsinit", "-log", str(tmp_path / "CDS.log"), "-restore", str(source)],
            cwd=tmp_path,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )
        try:
            process.wait(timeout=55)
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=8)
    text = (tmp_path / "stdout.log").read_text() + "\n" + (tmp_path / "CDS.log").read_text()
    text = "\n".join(line[3:] if line.startswith("\\o ") else line for line in text.splitlines())
    assert "AGENT_MENU_PASS" in text, text
    assert "*Error*" not in text, text
    assert "Fatal Python error" not in text, text
    assert "GE-2067" not in text, text
    assert process.returncode == 0
    prefix = "AGENT_MENU_CONTEXT "
    context = json.loads(
        next(line[len(prefix) :] for line in text.splitlines() if line.startswith(prefix))
    )
    assert context["valid"] is True
    assert context["cellview"]["cell"] == "first"
    prefix = "AGENT_CLOSED_CONTEXT "
    closed = json.loads(
        next(line[len(prefix) :] for line in text.splitlines() if line.startswith(prefix))
    )
    assert closed["valid"] is False
    assert len(list((tmp_path / ".cad/ai/agent/sessions").iterdir())) == 1
