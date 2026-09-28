from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from skill_test_support import rce_callback_loads

CAD_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.skipif(
    os.environ.get("RCE_RUN_SKILL_PROBE") != "1",
    reason="set RCE_RUN_SKILL_PROBE=1 to run the Cadence dbAccess probe",
)
def test_xrc_none_disables_parasitic_controls_with_dbaccess() -> None:
    dbaccess = shutil.which(os.environ.get("RCE_DBACCESS", "dbAccess"))
    if dbaccess is None:
        pytest.skip("dbAccess is unavailable")

    toml_helper = CAD_ROOT / "rce/skill/RCE_toml.il"
    skill = "\n".join(
        (
            f'load("{toml_helper}")',
            rce_callback_loads(),
            "defstruct(mockNoRcField value enabled invisible items)",
            "defstruct(mockNoRcParInfo value parasiticCoordinates "
            "parasiticResLayer parasiticResDimensions)",
            "defstruct(mockNoRcForm extTool rcType outType nameSource nlLay inpType "
            "rcePinOrderSuspended "
            "pinOrderBtn pinOrderType pinOrderFile bracketBtn bracketChoices "
            "hierDeliBtn hierDeliChoices netSelBtn netSelChoices netSelFile "
            "netSchSelBtn relFilterCapBtn relFilterCapValue absFilterCapBtn "
            "absFilterCapValue absFilterResBtn absFilterResValue nlParInfo nlRmInst)",
            "procedure(makeNoRcField(value) "
            "make_mockNoRcField(?value value ?enabled t ?invisible nil))",
            "coords=makeNoRcField(t)",
            "resLayer=makeNoRcField(t)",
            "resDimensions=makeNoRcField(t)",
            "parInfo=make_mockNoRcParInfo(?value list(t t t) "
            "?parasiticCoordinates coords ?parasiticResLayer resLayer "
            "?parasiticResDimensions resDimensions)",
            'form=make_mockNoRcForm(?extTool makeNoRcField("CalXRC") '
            '?inpType makeNoRcField("OA") '
            '?rcType makeNoRcField("NONE") ?outType makeNoRcField("dspf") '
            '?nameSource makeNoRcField("Schematic") ?nlLay makeNoRcField(nil) '
            '?pinOrderBtn makeNoRcField(nil) '
            '?pinOrderType makeNoRcField("CDL Netlist File") '
            '?pinOrderFile makeNoRcField("") ?bracketBtn makeNoRcField(nil) '
            '?bracketChoices makeNoRcField("Keep") '
            '?hierDeliBtn makeNoRcField(nil) '
            '?hierDeliChoices makeNoRcField("/") ?netSelBtn makeNoRcField(t) '
            '?netSelChoices makeNoRcField("Exclude Nets") '
            '?netSelFile makeNoRcField("nets.txt") '
            '?netSchSelBtn makeNoRcField(nil) '
            '?relFilterCapBtn makeNoRcField(t) '
            '?relFilterCapValue makeNoRcField("0.2") '
            '?absFilterCapBtn makeNoRcField(t) '
            '?absFilterCapValue makeNoRcField("0.02") '
            '?absFilterResBtn makeNoRcField(t) '
            '?absFilterResValue makeNoRcField("0.002") ?nlParInfo parInfo '
            '?nlRmInst makeNoRcField("TRUE"))',
            "rceSyncNetlistCustomize(form)",
            "noneState=and(!form~>netSelBtn~>value "
            "!form~>netSelBtn~>enabled !form~>netSelChoices~>enabled "
            "!form~>netSelFile~>enabled !form~>netSchSelBtn~>enabled "
            "!form~>relFilterCapBtn~>value !form~>relFilterCapBtn~>enabled "
            "!form~>relFilterCapValue~>enabled "
            "!form~>absFilterCapBtn~>value !form~>absFilterCapBtn~>enabled "
            "!form~>absFilterCapValue~>enabled "
            "!form~>absFilterResBtn~>value !form~>absFilterResBtn~>enabled "
            "!form~>absFilterResValue~>enabled "
            'form~>nlRmInst~>value=="FALSE" !form~>nlRmInst~>enabled '
            "form~>nlParInfo~>value==list(nil nil nil) "
            "!coords~>enabled !resLayer~>enabled !resDimensions~>enabled)",
            'form~>rcType~>value="RC"',
            "rceSyncNetlistCustomize(form)",
            "rcState=and(form~>netSelBtn~>enabled "
            "form~>relFilterCapBtn~>enabled form~>absFilterCapBtn~>enabled "
            "form~>absFilterResBtn~>enabled coords~>enabled "
            "resLayer~>enabled resDimensions~>enabled form~>nlRmInst~>enabled)",
            'if(and(noneState rcState) then printf("RCE_XRC_NONE_UI_OK\\n"))',
            "exit()",
        )
    )
    completed = subprocess.run(
        [dbaccess], input=skill, text=True, capture_output=True, timeout=30, check=False
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "*Error*" not in output
    assert "RCE_XRC_NONE_UI_OK" in output
