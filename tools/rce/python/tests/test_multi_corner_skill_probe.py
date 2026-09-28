from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from skill_probe_support import run_virtuoso_source
from skill_test_support import common_source_loads, rce_callback_loads, rce_source_loads

CAD_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.skipif(
    os.environ.get("RCE_RUN_SKILL_PROBE") != "1",
    reason="set RCE_RUN_SKILL_PROBE=1 to run the Cadence dbAccess probe",
)
def test_multi_corner_form_fields_are_created_by_dbaccess() -> None:
    dbaccess = shutil.which(os.environ.get("RCE_DBACCESS", "dbAccess"))
    if dbaccess is None:
        pytest.skip("dbAccess is unavailable")

    skill = "\n".join(
        (
            "defstruct(mockCornerGui name choices value items frame invisible scrollable)",
            "procedure(hiCreateRadioField(@key name prompt choices defValue itemsPerRow "
            "callback) make_mockCornerGui(?name name ?choices choices ?value defValue))",
            "procedure(hiCreateComboField(@key name prompt items value defValue editable "
            "callback invisible) make_mockCornerGui(?name name ?items items "
            "?value or(value defValue) ?invisible invisible))",
            "procedure(hiCreateToggleField(@key name choices value defValue itemsPerRow "
            "invisible) unless(and(choices listp(car(choices))) "
            "error(\"Each toggle item must be a list\")) "
            "make_mockCornerGui(?name name ?choices choices ?value value "
            "?invisible invisible))",
            "procedure(hiCreateStringField(@key name prompt value defValue editable "
            "callback) make_mockCornerGui(?name name ?value or(value defValue)))",
            "procedure(hiCreateLabel(@key name labelText justification) "
            "make_mockCornerGui(?name name ?value labelText))",
            "procedure(hiCreateHorizontalBoxLayout(name @key frame horiz_align spacing "
            "items invisible) make_mockCornerGui(?name name ?items items "
            "?invisible invisible))",
            "procedure(hiCreateVerticalBoxLayout(name @key frame horiz_align spacing "
            "items invisible scrollable) make_mockCornerGui(?name name ?items items "
            "?frame frame ?invisible invisible ?scrollable scrollable))",
            common_source_loads("gui"),
            rce_source_loads("gui"),
            "gui=makeInstance(quote(EXTOPTGUI))",
            "makeExtBasicOpt(gui)",
            "if(and(gui->cornerType gui->singleCornerSetup gui->multiCornerSetup "
            "gui->multiCornerSetup~>scrollable "
            "length(gui->cornerSelectFields)==32 "
            "length(gui->cornerNameFields)==32 "
            "length(gui->cornerTempFields)==32 "
            "length(gui->cornerRowNames)==32 "
            "length(gui->cornerRowLayouts)==32 "
            "cadddr(car(gui->cornerRowLayouts)~>items)==list('stretch_item 1) "
            "car(gui->cornerSelectFields)~>name==concat(\"rceCornerSelect01\") "
            "car(gui->cornerNameFields)~>name==concat(\"rceCornerName01\") "
            "car(gui->cornerSelectFields)~>invisible==nil "
            "car(gui->cornerNameFields)~>value==\" \" "
            "car(gui->cornerTempFields)~>invisible==nil "
            "car(gui->cornerRowLayouts)~>invisible==nil) "
            "then printf(\"RCE_MULTI_CORNER_FORM_CREATE_OK\\n\"))",
            "exit()",
        )
    )
    completed = subprocess.run(
        [dbaccess], input=skill, text=True, capture_output=True, timeout=30, check=False
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "RCE_MULTI_CORNER_FORM_CREATE_OK" in output


@pytest.mark.skipif(
    os.environ.get("RCE_RUN_SKILL_PROBE") != "1",
    reason="set RCE_RUN_SKILL_PROBE=1 to run the Cadence Virtuoso probe",
)
def test_multi_corner_form_and_rows_are_created_by_virtuoso(tmp_path: Path) -> None:
    virtuoso = shutil.which(os.environ.get("RCE_VIRTUOSO", "virtuoso"))
    if virtuoso is None:
        pytest.skip("virtuoso is unavailable")

    toml_helper = CAD_ROOT / "common/skill/SICO_toml.il"
    rce_helper = CAD_ROOT / "rce/skill/RCE_toml.il"
    batch_gui = CAD_ROOT / "common/skill++/BATCHGUI.ils"
    gui_source = CAD_ROOT / "rce/skill++/RCEGUI.ils"
    skill = "\n".join(
        (
            f'load("{toml_helper}")',
            f'load("{rce_helper}")',
            common_source_loads("gui"),
            f'load("{batch_gui}")',
            f'load("{CAD_ROOT / "common/skill++/PROFILEGUI.ils"}")',
            rce_source_loads("gui"),
            f'load("{CAD_ROOT / "rce/skill++/RCEREDUCTION.ils"}")',
            rce_callback_loads(),
            f'load("{gui_source}")',
            "gui=makeInstance(quote(EXTOPTGUI))",
            "makeExtBasicOpt(gui)",
            "form=hiCreateLayoutForm(quote(rceCornerProbe) "
            "\"RCE Corner Probe\" gui->tech)",
            "hiInstantiateForm(form)",
            "rceBindCornerRows(form length(gui->cornerRowNames))",
            "rcePopulateMultiCornerRows(form list(\"RCmax\" \"Cmin\") \"RCmax\")",
            "form~>multiCornerSetup~>invisible=nil",
            "initialRecords=rceCornerRowRecords(form)",
            "row3InitiallyHidden=form~>rceCornerRow03~>invisible",
            "rcePopulateMultiCornerRows(form "
            "list(\"Typ\" \"RCmin\" \"Cmax\") \"Typ\")",
            "updatedRecords=rceCornerRowRecords(form)",
            "hiDisplayForm(form)",
            "selectInfo=hiGetFieldInfo(form quote(rceCornerSelect01))",
            "tempInfo=hiGetFieldInfo(form quote(rceCornerTemp01))",
            "selectInfo2=hiGetFieldInfo(form quote(rceCornerSelect02))",
            "tempInfo2=hiGetFieldInfo(form quote(rceCornerTemp02))",
            "if(and(gui->cornerType gui->multiCornerSetup "
            "length(gui->cornerSelectFields)==32 "
            "form~>rceCornerRow01~>invisible==nil "
            "form~>rceCornerRow02~>invisible==nil "
            "row3InitiallyHidden form~>rceCornerRow03~>invisible==nil "
            "hiGetLayoutFrame(form quote(multiCornerSetup))==nil "
            "cadr(selectInfo)!=list(0 0) cadr(tempInfo)!=list(0 0) "
            "cadr(selectInfo2)!=list(0 0) cadr(tempInfo2)!=list(0 0) "
            "initialRecords==list(list(\"RCmax\" \"125\" t) "
            "list(\"Cmin\" \"125\" nil)) "
            "updatedRecords==list(list(\"Typ\" \"85\" t) "
            "list(\"RCmin\" \"25\" nil) list(\"Cmax\" \"25\" nil)) "
            "form~>rceCornerName01~>value==\"Typ\" "
            "form~>rceCornerName03~>value==\"Cmax\") "
            "then printf(\"RCE_REAL_MULTI_CORNER_FORM_OK\\n\"))",
            "exit()",
        )
    )
    output = run_virtuoso_source(skill, tmp_path)
    assert "RCE_REAL_MULTI_CORNER_FORM_OK" in output, output
