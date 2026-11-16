"""Actual ADE Assembler window and Results callback dispatch, without simulation."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

CAD = Path(__file__).absolute().parents[3]
IC = Path("/software/eda/cadence/installs/IC23.10.130/tools/dfII/bin/virtuoso")
pytestmark = pytest.mark.skipif(
    os.environ.get("SICO_IC_TESTS") != "1", reason="Opt-in real ADE Assembler test"
)


def test_ade_results_dispatch_retains_test_and_session(tmp_path):
    source = tmp_path / "ade.il"
    source.write_text(
        "\n".join(
            [
                'hiRegTimer("exit()" 400)',
                f"load({json.dumps(str(CAD / 'sico/skill++/SICO.ils'))})",
                f'ddCreateLib("AgentAdeMenu" {json.dumps(str(tmp_path / "library"))})',
                'cv=dbOpenCellViewByType("AgentAdeMenu" "tb" "schematic" "schematic" "w")',
                "dbSave(cv) dbClose(cv)",
                'background=maeOpenSetup("AgentAdeMenu" "tb" "maestro" ?application "Assembler")',
                'maeCreateTest("nominal" ?lib "AgentAdeMenu" ?cell "tb" ?view "schematic"',
                '  ?simulator "spectre" ?session background)',
                'maeAddOutput("gain" "nominal" ?expr "1" ?session background)',
                "maeSaveSetup(?session background) maeCloseSession(?session background)",
                'adeWin=geOpen(?lib "AgentAdeMenu" ?cell "tb" ?view "maestro")',
                "procedure(agentAdeLaunchProbe()",
                "  actualSession=axlGetWindowSession(adeWin)",
                '  unless(actualSession error("ADE did not open"))',
                '  toolSession=axlGetToolSession(actualSession "nominal")',
                "  menuItems=axlGetCustomOutputResultMenu(?axlSession actualSession",
                '    ?sevSession toolSession ?testName "nominal")',
                "  entry=car(last(menuItems))",
                '  unless(and(car(entry)=="Ask Silicon Copilot..." !caddr(entry))',
                '    error("Results menu is absent or disabled"))',
                "  hiSetCurrentWindow(hiGetCIWindow())",
                "  evalstring(cadr(entry))",
                "  composer=sicoQuickWindows[adeWin]",
                '  unless(composer error("Results callback did not start composer"))',
                '    printf("ADE_RESULT_CONTEXT %s\\n" sicoContextJson(composer[\'target]))',
                '    sicoQuickSend(composer "shutdown")',
                "    maeSaveSetup(?session actualSession)",
                "    maeCloseSession(?session actualSession ?forceClose t)",
                '    printf("ADE_RESULTS_PASS\\n") hiRegTimer("exit()" 30))',
                'hiRegTimer("agentAdeLaunchProbe()" 30)',
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
            process.wait(timeout=50)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
    text = (tmp_path / "CDS.log").read_text()
    assert "ADE_RESULTS_PASS" in text, text
    assert "*Error*" not in text and "Fatal Python error" not in text, text
    assert "GE-2067" not in text, text
    prefix = "\\o ADE_RESULT_CONTEXT "
    snapshot = json.loads(
        next(line[len(prefix) :] for line in text.splitlines() if line.startswith(prefix))
    )
    assert snapshot["valid"] is True
    assert snapshot["entry"] == "ade_results" and snapshot["selection_scope"] == "test"
    assert snapshot["test"] == "nominal" and snapshot["ade_session"].startswith("fnxSession")
    assert snapshot["cellview"] == {
        "lib": "AgentAdeMenu", "cell": "tb", "view_name": "maestro",
        "oa_view_type": None, "oa_view_type_source": "unknown",
    }


def test_ade_window_menu_entry_binds_the_ade_window(tmp_path):
    """The window menu makes Silicon Copilot reachable before any history exists."""
    source = tmp_path / "ade_menu.il"
    source.write_text(
        "\n".join(
            [
                'hiRegTimer("exit()" 400)',
                f"load({json.dumps(str(CAD / 'sico/skill++/SICO.ils'))})",
                f'ddCreateLib("AgentAdeWindow" {json.dumps(str(tmp_path / "library"))})',
                'cv=dbOpenCellViewByType("AgentAdeWindow" "tb" "schematic" "schematic" "w")',
                "dbSave(cv) dbClose(cv)",
                'background=maeOpenSetup("AgentAdeWindow" "tb" "maestro" ?application "Assembler")',
                'maeCreateTest("nominal" ?lib "AgentAdeWindow" ?cell "tb" ?view "schematic"',
                '  ?simulator "spectre" ?session background)',
                "maeSaveSetup(?session background) maeCloseSession(?session background)",
                'adeWin=geOpen(?lib "AgentAdeWindow" ?cell "tb" ?view "maestro")',
                "procedure(agentAdeWindowMenuWait()",
                "  agentAdeWindowMenuTicks=agentAdeWindowMenuTicks+1",
                "  adeProbeBanners=car(errset(hiGetBannerMenus(adeWin) nil))",
                "  if(member('sicoAdePulldown adeProbeBanners) then",
                "    agentAdeWindowMenuProbe()",
                "  else",
                "    if(agentAdeWindowMenuTicks>30 then",
                '      error("ADE window menu did not appear without a manual install")',
                '    else hiRegTimer("agentAdeWindowMenuWait()" 10))))',
                "procedure(agentAdeWindowMenuProbe()",
                "  actualSession=axlGetWindowSession(adeWin)",
                '  unless(actualSession error("ADE did not open"))',
                "  adeProbeKey=car(last(sicoMenuOrder))",
                "  adeProbeBinding=sicoMenuTargets[adeProbeKey]",
                '  unless(and(adeProbeBinding[\'window]==adeWin',
                '    adeProbeBinding[\'entry]=="ade_menu")',
                '    error("ADE window menu binding is wrong"))',
                "  hiSetCurrentWindow(hiGetCIWindow())",
                '  evalstring(sprintf(nil "sicoFromMenu(\\"%s\\")" adeProbeKey))',
                "  composer=sicoQuickWindows[adeWin]",
                '  unless(composer error("ADE window menu did not start the composer"))',
                '  printf("ADE_WINDOW_CONTEXT %s\\n" sicoContextJson(composer[\'target]))',
                '  sicoQuickSend(composer "shutdown")',
                "  maeSaveSetup(?session actualSession)",
                "  maeCloseSession(?session actualSession ?forceClose t)",
                '  printf("ADE_WINDOW_MENU_PASS\\n") hiRegTimer("exit()" 30))',
                "agentAdeWindowMenuTicks=0",
                'hiRegTimer("agentAdeWindowMenuWait()" 10)',
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
            process.wait(timeout=60)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
    text = (tmp_path / "CDS.log").read_text()
    assert "ADE_WINDOW_MENU_PASS" in text, text
    assert "*Error*" not in text and "Fatal Python error" not in text, text
    prefix = "\\o ADE_WINDOW_CONTEXT "
    snapshot = json.loads(
        next(line[len(prefix) :] for line in text.splitlines() if line.startswith(prefix))
    )
    assert snapshot["valid"] is True
    assert snapshot["entry"] == "ade_menu" and snapshot["selection_scope"] == "ade_session"
    assert snapshot["ade_session"].startswith("fnxSession")
    assert snapshot["cellview"] == {
        "lib": "AgentAdeWindow", "cell": "tb", "view_name": "maestro",
        "oa_view_type": None, "oa_view_type_source": "unknown",
    }
