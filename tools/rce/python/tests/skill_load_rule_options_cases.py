import re

from skill_load_fixtures import (
    CAD_ROOT,
    _procedure_body,
)
from skill_test_support import read_skill_source


def test_lvs_hcell_gui_is_wired_to_rce_and_standalone_lvs() -> None:
    base = read_skill_source(CAD_ROOT / "common/skill++/BASEGUI.ils")
    assert 'hcellFile=SICO_envValue("HCELL_FILE")' in base
    assert "hcellEnable=and(hcellFile strlen(hcellFile)>0)" in base
    assert "?defValue       hcellEnable" in base
    assert "?enabled  hcellEnable" in base

    for relative, callback in (
        ("rce/skill++/RCECB.ils", "rceLvsHcellCB"),
        ("lvs/skill++/LVSCB.ils", "lvsHcellCB"),
    ):
        text = read_skill_source(CAD_ROOT / relative)
        assert f"procedure({callback}(" in text
        assert "lvsHcellFile~>enabled=form~>lvsHcellBtn~>value" in text
        assert '"hcell_enable"' in text
        assert '"hcell_file"' in text

    for relative in ("rce/skill++/RCE.ils", "lvs/skill++/LVS.ils"):
        text = read_skill_source(CAD_ROOT / relative)
        assert "isCallable('makeLvsHcell)" in text


def test_ignore_lvs_error_is_standalone_only_and_svdb_query_is_lvs_only() -> None:
    lvs_gui = read_skill_source(CAD_ROOT / "lvs/skill++/LVSGUI.ils")
    lvs_cfg = read_skill_source(CAD_ROOT / "lvs/skill++/LVSCFG.ils")
    lvs_cb = read_skill_source(CAD_ROOT / "lvs/skill++/LVSCB.ils")
    rce_gui = read_skill_source(CAD_ROOT / "rce/skill++/RCEGUI.ils")

    assert "makeLvsIgnoreError(inst)" not in lvs_gui
    assert "lvsIgnoreErrorCB" not in lvs_gui + lvs_cfg + lvs_cb
    assert "makeLvsIgnoreError(inst)" in rce_gui
    assert '"SVDB query:"' in lvs_gui
    assert "?value      '(nil nil nil nil nil)" in lvs_gui
    assert "?defValue   '(nil nil nil nil nil)" in lvs_gui
    assert "gui->mainForm->svdbQuery->hiToolTip=list(" in lvs_gui
    for tooltip in (
        "Calibre Connectivity Interface",
        "LVS Recon or short isolation",
        "LVS Recon ERC",
        "ASCII cross-reference",
        "Pin locations",
    ):
        assert tooltip in lvs_gui
    assert '"CPU Number:"' in lvs_gui


def test_custom_svrf_is_shared_by_drc_lvs_and_rce() -> None:
    base = read_skill_source(CAD_ROOT / "common/skill++/BASEGUI.ils")
    method_start = base.index("defmethod(makeCustomSvrf")
    method_end = base.index(");defmethod makeCustomSvrf", method_start)
    method = base[method_start:method_end]

    assert "defclass(CUSTOMSVRFGUI ()" in base
    assert "defclass(LVSOPTGUI (CUSTOMSVRFGUI)" in base
    assert "defmethod(makeLvsCustomSvrf ((inst CUSTOMSVRFGUI))" in base

    for slot in (
        "customSvrfEnable",
        "customSvrfCommand",
        "customSvrf",
    ):
        assert f"({slot}" in base
    assert "hiCreateBooleanButton(" in method
    assert '?buttonText     "Customized SVRF Command"' in method
    assert re.search(r"[?]defValue\s+nil", method)
    assert "hiCreateMLTextField(" in method
    assert re.search(r'[?]value\s+""', method)
    assert re.search(r'[?]defValue\s+""', method)
    assert re.search(r"[?]invisible\s+t", method)
    assert "?hasVerticalScrollbar    t" in method
    assert "?hasHorizontalScrollbar  t" in method
    assert "?enableWordWrap          nil" in method
    assert "list(inst->customSvrfEnable 'horiz_align 'left)" in method
    assert "procedure(cadCustomSvrfCB(form)" in base
    assert (
        "form~>customSvrfCommand~>invisible="
        "!form~>customSvrfEnable~>value" in base
    )
    assert "hiSetFieldMinSize(form 'customSvrfCommand ?widgetHeight 120)" in base

    lvs_gui = read_skill_source(CAD_ROOT / "lvs/skill++/LVSGUI.ils")
    rce_gui = read_skill_source(CAD_ROOT / "rce/skill++/RCEGUI.ils")
    drc_gui = read_skill_source(CAD_ROOT / "drc/skill++/DRCGUI.ils")
    assert (
        "defclass(DRCGUI (PROFILEGUI INPUTGUI OUTPUTGUI HARDWARE "
        "CUSTOMSVRFGUI BATCHGUI)" in drc_gui
    )
    assert "makeCustomSvrf(inst)" in drc_gui
    assert "inst->customSvrf" in drc_gui
    assert "makeCustomSvrf(inst)" in lvs_gui
    assert lvs_gui.index("inst->lvsBasicOpt") < lvs_gui.index(
        "inst->customSvrf)", lvs_gui.index("defmethod(makeLvsOptLay")
    )
    for gui in (lvs_gui, rce_gui):
        assert "cadCustomSvrfBind(gui->mainForm)" in gui
    assert "cadCustomSvrfBind(gui->mainForm)" in drc_gui

    for relative in (
        "drc/skill++/DRCCB.ils",
        "lvs/skill++/LVSCB.ils",
        "rce/skill++/RCECB.ils",
    ):
        callback = read_skill_source(CAD_ROOT / relative)
        assert 'SICO_tomlWriteBool(outFile "custom_svrf_enable"' in callback
        assert 'SICO_tomlWriteString(outFile "custom_svrf_command"' in callback

    for relative, feature_revision, gui_revision in (
        (
            "lvs/skill++/LVS.ils",
            "lvsCustomSvrfRevision",
            "lvsCustomSvrfGuiRevision",
        ),
        (
            "rce/skill++/RCE.ils",
            "rceCustomSvrfRevision",
            "rceCustomSvrfGuiRevision",
        ),
    ):
        entry = read_skill_source(CAD_ROOT / relative)
        assert f"isCallable('{feature_revision})" in entry
        assert f'{feature_revision}()=="20260813.custom.svrf.common.v1"' in entry
        assert f"isCallable('{gui_revision})" in entry
        assert f'{gui_revision}()=="20260813.custom.svrf.common.v1"' in entry
        assert "isCallable('cadBaseLvsCustomSvrfRevision)" in entry
        assert (
            'cadBaseLvsCustomSvrfRevision()=="20260812.custom.svrf.align.v2"'
            in entry
        )
        assert "isCallable('makeLvsCustomSvrf)" in entry
        assert "isCallable('cadLvsCustomSvrfBind)" in entry

    assert 'procedure(lvsCustomSvrfGuiRevision()' in lvs_gui
    assert 'procedure(rceCustomSvrfGuiRevision()' in rce_gui
    drc_entry = read_skill_source(CAD_ROOT / "drc/skill++/DRC.ils")
    assert 'cadBaseCustomSvrfRevision()=="20260813.custom.svrf.common.v1"' in drc_entry
    assert 'drcCustomSvrfRevision()=="20260813.custom.svrf.common.v1"' in drc_entry
    assert 'drcCustomSvrfGuiRevision()=="20260813.custom.svrf.common.v1"' in drc_entry


def test_cdl_include_is_wired_to_rce_lvs_and_export_cdl() -> None:
    base = read_skill_source(CAD_ROOT / "common/skill++/BASEGUI.ils")
    method_start = base.index("defmethod(makeCdlInclude")
    method_end = base.index(");defmethod makeCdlInclude", method_start)
    method = base[method_start:method_end]

    for slot in ("cdlIncludeBtn", "cdlIncludeFile", "cdlInclude"):
        assert f"({slot}" in base
    assert 'includeFile=SICO_envValue("CDL_HEADER_FILE")' in method
    assert "includeEnable=and(includeFile strlen(includeFile)>0)" in method
    assert '?buttonText     "CDL Include:"' in method
    assert "?buttonLocation 'left" in method
    assert "?defValue       includeEnable" in method
    assert "?mode     'existingFile" in method
    assert '?defValue or(includeFile "")' in method
    assert "?enabled  includeEnable" in method
    assert "procedure(cadCdlIncludeCB(form)" in base
    assert "procedure(cadCdlIncludeInputTypeCB(form)" in base
    assert (
        "form~>cdlIncludeFile~>enabled=form~>cdlIncludeBtn~>value" in base
    )
    assert '!member(form~>inpType~>value \'("OA" "SCH+GDS"))' in base

    lvs_gui = read_skill_source(CAD_ROOT / "lvs/skill++/LVSGUI.ils")
    rce_gui = read_skill_source(CAD_ROOT / "rce/skill++/RCEGUI.ils")
    stage_gui = read_skill_source(CAD_ROOT / "lvs/skill++/STAGEGUI.ils")
    assert stage_gui.count("?initialSize   500:450") == 2
    assert "?initialSize   520:320" not in stage_gui
    for gui in (lvs_gui, rce_gui):
        assert "makeCdlInclude(inst)" in gui
        assert "inst->cdlInclude" in gui
    for gui in (lvs_gui, rce_gui):
        input_start = gui.index("defmethod(makeInpLay")
        input_end = gui.index("\ndefmethod(", input_start + 1)
        input_layout = gui[input_start:input_end]
        assert "'inputMoreContents list(inst->cdlInclude" in input_layout
        assert '?labelText   "More Input Options"' in input_layout
        assert "?onFields    '(inputMoreContents)" in input_layout
    export_start = stage_gui.index("defmethod(makeInpLay ((inst EXPORTCDLGUI))")
    export_end = stage_gui.index("\ndefmethod(", export_start + 1)
    export_input = stage_gui[export_start:export_end]
    assert "makeCdlInclude(inst)" in export_input
    assert "inst->cdlInclude" in export_input
    stream_start = stage_gui.index("defmethod(makeInpLay ((inst STREAMGDSGUI))")
    stream_end = stage_gui.index("\ndefmethod(", stream_start + 1)
    assert "makeCdlInclude(inst)" not in stage_gui[stream_start:stream_end]

    rce_callback = read_skill_source(CAD_ROOT / "rce/skill++/RCECB.ils")
    input_callback = _procedure_body(rce_callback, "rceInpCB")
    assert "cadCdlIncludeInputTypeCB(form)" in input_callback
    lvs_callback = read_skill_source(CAD_ROOT / "lvs/skill++/LVSCB.ils")
    assert "cadCdlIncludeInputTypeCB(form)" in _procedure_body(
        lvs_callback, "lvsInpCB"
    )

    writers = (
        ("lvs/skill++/LVSCB.ils", "lvsCdlIncludeRevision"),
        ("rce/skill++/RCECB.ils", "rceCdlIncludeRevision"),
        ("lvs/skill++/STAGECB.ils", "lvsStageCdlIncludeRevision"),
    )
    for relative, revision in writers:
        writer = read_skill_source(CAD_ROOT / relative)
        assert 'getShellEnvVar("CDL_HEADER_FILE")' not in writer
        assert "cdlIncludeFile=form~>cdlIncludeFile~>value" in writer
        assert "isFile(cdlIncludeFile)" in writer
        assert "cdlIncludeFile=SICO_absolutePath(cdlIncludeFile)" in writer
        assert 'SICO_tomlWriteString(outFile "cdl_header_file" cdlIncludeFile)' in writer
        assert f"procedure({revision}()" in writer

    revision = "20260812.cdl.include.v2"
    entries = (
        ("lvs/skill++/LVS.ils", "lvsCdlIncludeGuiRevision"),
        ("rce/skill++/RCE.ils", "rceCdlIncludeGuiRevision"),
    )
    for relative, gui_revision in entries:
        entry = read_skill_source(CAD_ROOT / relative)
        assert "isCallable('cadBaseCdlIncludeRevision)" in entry
        assert f'cadBaseCdlIncludeRevision()=="{revision}"' in entry
        assert f"isCallable('{gui_revision})" in entry
        assert (
            f'{gui_revision}()=="20260812.cdl.include.layout.v3"' in entry
        )
        assert "isCallable('makeCdlInclude)" in entry
        assert "isCallable('cadCdlIncludeCB)" in entry
        assert "isCallable('cadCdlIncludeInputTypeCB)" in entry

    rce_batch = read_skill_source(CAD_ROOT / "rce/skill++/RCEBATCH.ils")
    assert "procedure(rceBatchEnterMultiple(form)" in rce_batch
    assert "cadCdlIncludeInputTypeCB(form)" in rce_batch
    assert "procedure(rceCdlIncludeBatchRevision()" in rce_batch
    rce_entry = read_skill_source(CAD_ROOT / "rce/skill++/RCE.ils")
    assert "isCallable('rceCdlIncludeBatchRevision)" in rce_entry
    assert f'rceCdlIncludeBatchRevision()=="{revision}"' in rce_entry

    lvs_batch = read_skill_source(CAD_ROOT / "lvs/skill++/LVSRUN.ils")
    assert "procedure(lvsBatchEnterMultiple(form)" in lvs_batch
    assert "cadCdlIncludeInputTypeCB(form)" in lvs_batch
    assert "procedure(lvsCdlIncludeBatchRevision()" in lvs_batch
    lvs_entry = read_skill_source(CAD_ROOT / "lvs/skill++/LVS.ils")
    assert "isCallable('lvsCdlIncludeBatchRevision)" in lvs_entry
    assert f'lvsCdlIncludeBatchRevision()=="{revision}"' in lvs_entry


def test_lvs_and_rce_virtual_connect_name_defaults_and_callbacks() -> None:
    base = read_skill_source(CAD_ROOT / "common/skill++/BASEGUI.ils")
    toggle_start = base.index("defmethod(makeLvsVirtualConn ")
    toggle_end = base.index(");defmethod makeLvsVirtualConn", toggle_start)
    toggle = base[toggle_start:toggle_end]
    name_start = base.index("defmethod(makeLvsVirtualConnName")
    name_end = base.index(");demethod makeLvsVirtualconnName", name_start)
    name_field = base[name_start:name_end]

    assert re.search(r"[?]value\s+'[(]nil t[)]", toggle)
    assert re.search(r"[?]defValue\s+'[(]nil t[)]", toggle)
    assert re.search(r'[?]value\s+"[?]"', name_field)
    assert re.search(r'[?]defValue\s+"[?]"', name_field)
    assert re.search(r"[?]invisible\s+nil", name_field)

    for relative, callback_name in (
        ("lvs/skill++/LVSCB.ils", "lvsVirtualConnCB"),
        ("rce/skill++/RCECB.ils", "rceLvsVirtualConnCB"),
    ):
        text = read_skill_source(CAD_ROOT / relative)
        body = _procedure_body(text, callback_name)
        assert "nameEnabled=cadr(form->lvsVirtualConn->value)" in body
        assert "form->lvsVirtualConnName->invisible=nil" in body
        assert "blankstrp(form->lvsVirtualConnName->value)" in body
        assert 'form->lvsVirtualConnName->value="?"' in body
        assert "form->lvsVirtualConnName->invisible=t" in body


def test_lvs_case_sensitive_matching_is_enabled_by_default() -> None:
    base = read_skill_source(CAD_ROOT / "common/skill++/BASEGUI.ils")
    start = base.index("defmethod(makeLvsCase")
    end = base.index(");defmethod makeLvsCase", start)
    method = base[start:end]

    assert '?buttonText     "Case Sensitive"' in method
    assert re.search(r"[?]defValue\s+t", method)
