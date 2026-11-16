"""SKILL callback composition tests; separate from the real ADE GUI acceptance."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

CAD = Path(__file__).absolute().parents[3]
DB = Path("/software/eda/cadence/installs/IC23.10.130/tools/dfII/bin/dbAccess")


@pytest.mark.skipif(not DB.is_file(), reason="IC231 dbAccess is not installed")
def test_site_callbacks_preserved_after_reload_and_targets_captured(tmp_path):
    loader = str(CAD / "sico/skill++/SICO.ils")
    lines = [
        "siteHistoryCalls=0 siteResultArgs=nil",
        "procedure(axlGetDataViewHistoryUserMenu(session history)",
        '  siteHistoryCalls=siteHistoryCalls+1 list(list("Site History" "site()" "false")))',
        "procedure(axlGetCustomOutputResultMenu(@rest args)",
        '  siteResultArgs=args list(list("Site Results" "site()" nil)))',
        f'load("{loader}")',
        f'load("{loader}")',
        "procedure(hiGetWindowList(@optional kind) list(1 2))",
        "procedure(hiRegCloseProc(window callback) t)",
        "procedure(hiGetCurrentWindow() 2)",
        'procedure(axlGetWindowSession(window) if(window==1 then "sessionA" else "sessionB"))',
        'procedure(geGetWindowCellView(window) error("Graphical getter used for ADE"))',
        'procedure(axlGetSessionLibName(session) "library")',
        'procedure(axlGetSessionCellName(session) "tb")',
        'procedure(axlGetSessionViewName(session) "maestro")',
        "procedure(axlGetMainSetupDB(session) session)",
        'procedure(axlGetTests(sdb) list(1 list("nominal")))',
        "procedure(axlGetToolSession(session test)",
        '  if(session=="sessionA" then "sev1" else "sev2"))',
        'procedure(axlGetHistoryName(history) strcat("Interactive." sprintf(nil "%d" history)))',
        "historyLookup=7",
        "procedure(axlGetHistoryEntry(sdb name) historyLookup)",
        'historyItems=axlGetDataViewHistoryUserMenu("sessionA" 7)',
        'unless(and(siteHistoryCalls==1 length(historyItems)==2) error("History composition"))',
        "captured=sicoMenuTargets[car(last(sicoMenuOrder))]",
        "unless(and(captured['window]==1 captured['historyName]==\"Interactive.7\"",
        "  captured['valid])",
        '  error("History binding"))',
        "historyLookup=8 sicoContextJson(captured)",
        'when(captured[\'valid] error("Replaced History handle still valid"))',
        'args=list(?axlSession "sessionA" ?sevSession "sev1" ?testName "nominal")',
        "resultsItems=apply('axlGetCustomOutputResultMenu args)",
        'unless(and(siteResultArgs==args length(resultsItems)==2) error("Results composition"))',
        "captured=sicoMenuTargets[car(last(sicoMenuOrder))]",
        "unless(and(captured['window]==1 captured['testName]==\"nominal\"",
        '  captured[\'valid]) error("Results binding"))',
        "procedure(sicoQuickAsk(captured) lastWindow=captured['window] lastBinding=captured)",
        "evalstring(cadr(cadr(resultsItems)))",
        'unless(lastWindow==1 error("Callback followed focus"))',
        'captured[\'testName]="removed" sicoContextJson(captured)',
        'when(captured[\'valid] error("Deleted test still valid"))',
        'when(errset(aiCopilotSourceCheck(captured) nil) error("Invalid ADE source accepted for circuit dispatch"))',
        'legacyItems=axlGetCustomOutputResultMenu(?adeSession "sev1" ?testName "nominal")',
        "captured=sicoMenuTargets[car(last(sicoMenuOrder))]",
        'unless(and(captured[\'window]==1 !caddr(cadr(legacyItems))) error("Legacy session"))',
        'missingItems=axlGetCustomOutputResultMenu(?adeSession "missing")',
        'unless(caddr(cadr(missingItems)) error("Missing explicit session fell back to focus"))',
        'printf("AGENT_HOOKS_PASS\\n") exit()',
    ]
    source = tmp_path / "hooks.il"
    source.write_text("\n".join(lines) + "\n")
    env = {
        k: v
        for k, v in os.environ.items()
        if k
        not in {
            "LD_LIBRARY_PATH",
            "LD_PRELOAD",
            "SICO_ROOT",
        }
    }
    result = subprocess.run(
        [str(DB), "-load", str(source)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert "AGENT_HOOKS_PASS" in result.stdout, result.stdout + result.stderr
    assert "*Error*" not in result.stdout + result.stderr
