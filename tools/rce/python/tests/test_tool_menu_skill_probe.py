"""Opt-in probe: the RCE EXT Engine radio loads for every RCE_DEF_TOOL."""

from __future__ import annotations

from pathlib import Path

import pytest

from absolute_path_probe_support import RUN_PROBE, _install_tree, _write
from skill_probe_support import run_virtuoso_source


@pytest.mark.skipif(
    not RUN_PROBE, reason="set RCE_RUN_SKILL_PROBE=1 to run the Virtuoso probe"
)
@pytest.mark.parametrize(
    ("configured", "selected"),
    [
        ("CalXRC", "CalXRC"),
        ("CalXACT", "CalXACT"),
        ("XRC", "CalXRC"),
        ("StarRC", "StarRC"),
        ("unusable", "StarRC"),
    ],
)
def test_ext_tool_radio_builds_for_configured_default(
    tmp_path: Path,
    configured: str,
    selected: str,
) -> None:
    install = _install_tree(tmp_path)
    launch = tmp_path / "launch"
    _write(launch / "cds.lib")
    _write(launch / "tech/Typ/xrc.cal")
    log = launch / "tool_menu.log"
    replay = launch / "tool_menu.il"
    replay.write_text(
        "\n".join(
            (
                f'load("{install}/tools/rce/skill++/RCE.ils")',
                "cadDisplayRceForm()",
                "form=rceForm",
                'unless(equal(form~>extTool~>choices'
                ' list("StarRC" "QRC" "CalXRC" "CalXACT"))'
                ' error("Unexpected EXT Engine choices"))',
                f'unless(form~>extTool~>value=="{selected}"'
                ' error("Unexpected EXT Engine default"))',
                'unless(RCE_setExtToolName(form "CalXACT")'
                ' error("CalXACT restore failed"))',
                'unless(form~>extTool~>value=="CalXACT"'
                ' error("CalXACT restore produced %L" form~>extTool~>value))',
                'unless(RCE_setExtToolName(form "XRC")'
                ' error("XRC alias restore failed"))',
                'unless(form~>extTool~>value=="CalXRC"'
                ' error("XRC alias restore produced %L" form~>extTool~>value))',
                'unless(RCE_calibrePexToolP(form~>extTool~>value)'
                ' error("CalXRC is not recognized as Calibre PEX"))',
                # Switching the engine must keep the tool-aware controls in
                # t/nil state; a member() result would raise a HI "Bad value"
                # warning for startRve and friends.
                'foreach(tool list("StarRC" "QRC" "CalXRC" "CalXACT")',
                "  form~>extTool~>value=tool",
                "  rceExtToolCB(form)",
                '  unless(member(form~>startRve~>enabled list(t nil))'
                ' error("startRve enabled is not t/nil for %s" tool))',
                '  unless(member(form~>startRve~>invisible list(t nil))'
                ' error("startRve invisible is not t/nil for %s" tool))',
                '  unless(eq(form~>startRve~>invisible'
                "    !RCE_calibrePexToolP(tool))"
                ' error("startRve visibility is wrong for %s" tool))',
                ')',
                'when(form hiFormDone(form) hiDeleteForm(form))',
                'printf("RCE_TOOL_MENU_OK\\n")',
                "exit()",
            )
        )
        + "\n",
        encoding="utf-8",
    )

    output = run_virtuoso_source(
        replay.read_text(encoding="utf-8"),
        launch,
        env_updates={"CAD_HOME": str(install), "RCE_DEF_TOOL": configured},
        log_path=log,
    )

    assert "RCE_TOOL_MENU_OK" in output
