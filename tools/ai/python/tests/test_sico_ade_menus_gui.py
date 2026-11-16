"""Licensed ADE callback registration and menu-factory evidence."""

import os
from pathlib import Path

import pytest

from cadai.headless_session import session

SKILL = Path(__file__).resolve().parents[3] / "sico/skill/SICO_menus.il"


@pytest.mark.skipif(
    os.environ.get("SICO_AI_RUN_GUI_TESTS") != "1", reason="licensed ADE menu acceptance"
)
def test_sico_installs_ade_callbacks_and_menu_factory(tmp_path):
    with session(tmp_path) as (bridge, _server, _process):
        assert bridge.skill(
            "load(" + repr(str(SKILL)).replace("'", '"') + ")") == "t"
        assert bridge.skill("sicoMenusRevision()") == "20261020.sico.menus.v2"
        assert bridge.skill("sicoInstallAdeMenus()") == "t"
        assert bridge.skill(
            "getd('axlGetDataViewHistoryUserMenu)==getd('sicoHistoryMenu)") == "t"
        assert bridge.skill(
            "getd('axlGetCustomOutputResultMenu)==getd('sicoResultsMenu)") == "t"
        menu = bridge.skill("sicoAdeWindowMenu(hiGetCurrentWindow())")
        assert menu.startswith("hiMenu@")
        assert bridge.skill("sicoMenuTargets[car(sicoMenuOrder)]['entry]") == "ade_menu"
        history = bridge.skill("errset(sicoHistoryMenu('session nil) nil)")
        assert "Ask Silicon Copilot..." in history
        bridge.skill("errset(sicoResultsMenu() nil)")
        assert bridge.skill(
            "mapcar(lambda((k) sicoMenuTargets[k]['entry]) sicoMenuOrder)"
        ).count("ade_results") == 1
        # A CIW-only headless session has no ADE window to insert into. Record
        # that boundary rather than claiming a live ADE banner check.
        assert bridge.skill("sicoInstallAdeWindowMenus()") == "0"
        assert bridge.skill("sicoAdeWindowP(hiGetCurrentWindow())") == "nil"
