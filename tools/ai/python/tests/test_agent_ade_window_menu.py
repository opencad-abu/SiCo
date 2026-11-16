"""ADE window menu installation; the data view page itself has no user hook."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

CAD = Path(__file__).absolute().parents[3]
DB = Path("/software/eda/cadence/installs/IC23.10.130/tools/dfII/bin/dbAccess")


@pytest.mark.skipif(not DB.is_file(), reason="IC231 dbAccess is not installed")
def test_ade_window_menu_install_is_idempotent_reinstalled_and_polled(tmp_path):
    loader = str(CAD / "sico/skill++/SICO.ils")
    lines = [
        "adeTestWindows=list(1 2 3)",
        'adeTestSessions=makeTable("sessions" nil)',
        'adeTestSessions[1]="sessionA"',
        'adeTestSessions[2]="sessionA"',
        'adeTestSessions[3]="sessionC"',
        "adeTestCv=makeTable(\"cv\" nil) adeTestCv['cellViewType]=\"schematic\"",
        "procedure(hiGetCIWindow() 'ciw)",
        'procedure(hiGetAppType(win) if(win==2 "explorer-schematic" "explorer"))',
        "procedure(geIsGraphicalWindow(win noWarn) win==2)",
        "procedure(geGetWindowCellView(win) adeTestCv)",
        "procedure(geGetEditCellView(win) adeTestCv)",
        "procedure(geGetInstHierPath(win) nil)",
        'adeTestBanners=makeTable("banners" nil)',
        "adeTestBanners[1]=list('maestroTools 'axlFileMenu1 'axlToolsMenu1 'axlOptionsMenu1)",
        "adeTestBanners[2]=list('maestroTools 'axlFileMenu1)",
        "adeTestBanners[3]=nil",
        "adeTestInserted=list() adeTestTimers=list()",
        "procedure(hiGetWindowList(@optional kind) adeTestWindows)",
        "procedure(hiGetBannerMenus(win) adeTestBanners[win])",
        "procedure(hiInsertBannerMenu(win menu position)",
        "  adeTestInserted=append1(adeTestInserted list(win menu position))",
        "  adeTestBanners[win]=append1(adeTestBanners[win] menu) t)",
        "procedure(hiRegTimer(command delay)",
        "  adeTestTimers=append1(adeTestTimers list(command delay)) t)",
        "procedure(axlGetWindowSession(win) adeTestSessions[win])",
        "procedure(sicoAskIcon() nil)",
        "procedure(hiCreatePulldownMenu(@rest args) 'sicoAdePulldown)",
        "procedure(hiCreateMenuItem(@rest args) 'sicoAdeAsk)",
        f'load("{loader}")',
        "unless(length(adeTestInserted)==1"
        " error(sprintf(nil \"window menu installed once: %L\" adeTestInserted)))",
        "unless(car(car(adeTestInserted))==1 error(\"non-ADE window received the menu\"))",
        "unless(cadr(car(adeTestInserted))=='sicoAdePulldown error(\"unexpected menu symbol\"))",
        "unless(caddr(car(adeTestInserted))==3 error(\"menu position must follow Tools\"))",
        "unless(cadr(adeTestBanners[2])=='axlFileMenu1 error(\"non-ADE banner changed\"))",
        "unless(length(adeTestTimers)==1 error(\"poll must be armed once\"))",
        "unless(cadr(car(adeTestTimers))==20 error(\"poll tick must be ~2 s\"))",
        "unless(car(car(adeTestTimers))==\"sicoAdeMenuPoll()\""
        " error(\"unexpected poll command\"))",
        "key=car(last(sicoMenuOrder)) captured=sicoMenuTargets[key]",
        "unless(and(captured['window]==1 captured['entry]==\"ade_menu\")"
        " error(\"unexpected ADE menu binding\"))",
        "unless(sicoInstallAdeWindowMenus()==0 error(\"unchanged banners reinstall\"))",
        'unless(sicoWindowForSession("sessionA")==1 error("ADE lookup chose associated schematic"))',
        'procedure(axlGetMainSetupDB(session) session)',
        'procedure(axlGetTests(session) list(nil list("nominal")))',
        'procedure(axlGetToolSession(session test) strcat(session "_tool"))',
        'unless(sicoWindowForResult("sessionA_tool" "nominal")==1',
        '  error("Results lookup chose associated schematic"))',
        "adeTestBanners[1]=list('maestroTools 'axlFileMenu1 'axlToolsMenu1 'axlOptionsMenu1)",
        "unless(sicoInstallAdeWindowMenus()==1 error(\"ADE rebuild not repaired\"))",
        "unless(caddr(car(last(adeTestInserted)))==3 error(\"repaired menu position\"))",
        "sicoAdeMenuPoll()",
        "unless(and(sicoAdeMenuPolling length(adeTestTimers)==2)"
        " error(\"poll did not re-arm\"))",
        'printf("ADE_WINDOW_MENU_PASS\\n") exit()',
    ]
    source = tmp_path / "ade_window_menu.il"
    source.write_text("\n".join(lines) + "\n")
    env = {
        k: v
        for k, v in os.environ.items()
        if k and k not in {"LD_LIBRARY_PATH", "LD_PRELOAD", "SICO_ROOT"}
    }
    result = subprocess.run(
        [str(DB), "-load", str(source)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=40,
    )
    assert "ADE_WINDOW_MENU_PASS" in result.stdout, result.stdout + result.stderr
    assert "*Error*" not in result.stdout + result.stderr, result.stdout + result.stderr
