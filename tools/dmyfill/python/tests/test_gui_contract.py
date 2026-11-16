from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_menu_exposes_dummy_fill() -> None:
    text = (ROOT.parent / "common/skill/gui/SICO_toolsMenu.il").read_text(
        encoding="utf-8"
    )
    assert 'list("dmyfill" "Dummy Fill..." "SICO_dmyfillDisplayForm()")' in text


def test_form_runs_the_three_stage_flow_and_does_not_save() -> None:
    text = (ROOT / "skill/SICO_dmyfillGui.il").read_text(encoding="utf-8")
    assert "Current Cell" in text
    assert "Visible Window" in text
    assert "Selected Box" in text
    assert "dmyfillLayerBox" in text
    assert "dmyfillBox" in text
    assert "dmyfillSelectBox" in text
    assert "Start to select" in text
    assert "dmyfillBoxRow" in text
    assert "hiCreateBooleanButton" in text
    assert "hiCreateHorizontalBoxLayout" in text
    assert "enterBox" in text
    assert "dmyfillProfile" not in text
    assert "dmyfillLayerMap" not in text
    assert "SICO_dmyfillResolveProfile" in text
    assert "SICO_dmyfillStart" in text
    assert "dbSave" not in text


def test_run_flow_exports_gds_runs_backend_and_applies_plan() -> None:
    text = (ROOT / "skill/SICO_dmyfillRun.il").read_text(encoding="utf-8")
    assert "SICO_dmyfillDrcRunRoot" in text
    assert 'getShellEnvVar("DRC_DB_DIR")' in text
    assert 'cv~>libName "." cv~>cellName' in text
    assert "dmyfillXXXX" in text
    assert "SICO_tempDir" not in text
    assert "SICO_dmyfillExportGds" in text
    assert "SICO_dmyfillPythonCommand" in text
    assert "ipcBeginProcess" in text
    assert "SICO_dmyfillIpcPostFunc" in text
    assert "SICO_dmyfillFinishQueued" in text
    assert "SICO_dmyfillLogViewOpen" in text
    assert "SICO_dmyfillFlowLogOpen" in text
    assert "SICO_dmyfillFlowLogShow" in text
    assert "GUI_flowLogOpen" in text
    assert "GUI_flowLogShow" in text
    assert "GUI_flowLogClose" in text
    assert "UI_flowLog.il" in text
    assert "hiTextDisplayString" not in text
    assert "hiScrollWindowBottom" not in text
    assert "strmout.log" in text
    assert "TECH_LAYER_MAP" in text
    assert "GDS_OBJECT_MAP" in text
    assert "SICO_dmyfillApplyPlanToCv" in text
    assert "strmout" in text
