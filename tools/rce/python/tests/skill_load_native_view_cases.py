import re

from skill_load_fixtures import (
    CAD_ROOT,
    _procedure_body,
)
from skill_test_support import read_skill_source


def test_rce_top_cell_and_name_source_gui_and_toml_are_wired() -> None:
    shared_base = read_skill_source(CAD_ROOT / "common/skill++/BASEGUI.ils")
    base = shared_base + read_skill_source(CAD_ROOT / "rce/skill++/RCEBASEGUI.ils")
    config = read_skill_source(CAD_ROOT / "rce/skill++/RCECFG.ils")
    gui = read_skill_source(CAD_ROOT / "rce/skill++/RCEGUI.ils")
    callback = read_skill_source(CAD_ROOT / "rce/skill++/RCECB.ils")

    assert "(cellNameCB   @initform nil" in base
    assert base.count("?callback inst->cellNameCB") == 6
    assert re.search(r'\?prompt\s+"Top\s+Cell\s+Name:"', base)
    assert '?choices     inst->topCellSourceChoices' in base
    assert '?defValue    "Schematic"' in base
    assert "?itemsPerRow 2" in base
    assert "?name     'extractTopCell" in base
    assert "?editable nil" in base
    assert "inst->nameSource=hiCreateRadioField(" in base
    assert "?name        'nameSource" in base
    assert re.search(r'\?prompt\s+"Use\s+Name\s+From:"', base)
    assert "?choices     inst->nameSourceChoices" in base
    assert base.count('?defValue    "Schematic"') >= 2
    assert base.index("inst->extractTopCellRow=makehbl(") < base.index(
        "inst->nameSource=hiCreateRadioField("
    )
    assert base.index("inst->rcType=hiCreateRadioField(") < base.index(
        "inst->topCellSource=hiCreateRadioField("
    )
    assert '?labelText   "More Extraction Options"' in base
    assert (
        "?onFields    '(extractTopCellRow nameSource netSel cellSel filter)"
        in base
    )

    assert '?cellNameCB           "(rceSyncExtractTopCell hiGetCurrentForm())"' in config
    assert "(rceTopCellSourceCB hiGetCurrentForm())" in config
    assert "gui->cellNameCB           = extstrStruc->cellNameCB" in gui
    assert "gui->topCellSourceCB      = extstrStruc->topCellSourceCB" in gui
    assert "gui->nameSourceCB         = extstrStruc->nameSourceCB" in gui
    assert "rceSyncExtractTopCell(gui->mainForm)" in gui
    assert "?callback    inst->nameSourceCB" in base
    assert "rceNameSourceCB hiGetCurrentForm()" in config

    for field in ("schCell", "layCell", "cdlCell", "gdsCell", "svdbCell", "cciCell"):
        assert f"form~>{field}~>value" in callback
    assert "form~>topCellSource~>enabled=!singleCell" in callback
    assert 'form~>topCellSource~>value="Schematic"' not in callback
    assert 'form~>topCellSource~>value="Layout"' in callback
    assert callback.count("rceSyncExtractTopCell(form)") >= 6
    assert "topCell=rceSyncExtractTopCell(form)" in callback
    assert 'SICO_tomlWriteString(outFile "top_cell_source"' in callback
    assert 'SICO_tomlWriteString(outFile "name_source"' in callback
    assert "lowerCase(form~>nameSource~>value)" in callback
    assert "outPath=if(nativeView then" in callback
    assert "RCE_tomlDefaultOutput(runDir topCell" in callback


def test_rce_advanced_options_are_grouped_under_disclosures() -> None:
    base = read_skill_source(CAD_ROOT / "rce/skill++/RCEBASEGUI.ils")
    gui = read_skill_source(CAD_ROOT / "rce/skill++/RCEGUI.ils")
    entry = read_skill_source(CAD_ROOT / "rce/skill++/RCE.ils")

    assert '?labelText   "More Extraction Options"' in base
    assert (
        "?onFields    '(extractTopCellRow nameSource netSel cellSel filter)"
        in base
    )
    assert "'inputMoreContents list(inst->cdlInclude" in gui
    assert '?labelText   "More Input Options"' in gui
    assert "?onFields    '(inputMoreContents)" in gui
    assert "'lvsMoreContents list(inst->lvsRecognizeGates inst->customSvrf)" in gui
    assert '?labelText   "More LVS Options"' in gui
    assert "?onFields    '(lvsMoreContents)" in gui
    assert gui.count("?persistent  nil") >= 2
    assert "procedure(rceAdvancedOptionsGuiRevision()" in gui
    assert (
        'rceAdvancedOptionsGuiRevision()=="20260817.advanced.options.v1"'
        in entry
    )


def test_rce_oa_input_names_can_be_synchronized_in_either_direction() -> None:
    base = read_skill_source(CAD_ROOT / "common/skill++/BASEGUI.ils")
    gui = read_skill_source(CAD_ROOT / "rce/skill++/RCEGUI.ils")
    callback = read_skill_source(CAD_ROOT / "rce/skill++/RCECB.ils")
    entry = read_skill_source(CAD_ROOT / "rce/skill++/RCE.ils")

    for slot in ("inputSync", "schToLay", "layToSch"):
        assert f"({slot}" in gui
    assert "defmethod(makeInputNameSync ((inst RCEGUI))" in gui
    assert '?buttonText "->"' in gui
    assert '?buttonText "<-"' in gui
    assert 'Copy Schematic Library/Cell to Layout' in gui
    assert 'Copy Layout Library/Cell to Schematic' in gui
    assert 'rceSyncInputNames hiGetCurrentForm() \\"Schematic\\"' in gui
    assert 'rceSyncInputNames hiGetCurrentForm() \\"Layout\\"' in gui
    assert "inst->inputSync=makevbl('inputSync" in gui
    assert "list(list(inst->frontendInp 'stretch 1)" in gui
    assert "list(inst->backendInp 'stretch 1)" in gui
    lay_view_start = base.index("layView=ddHiCreateViewComboField(")
    lay_view_end = base.index("inst->layInp=", lay_view_start)
    assert '?defValue "layout"' in base[lay_view_start:lay_view_end]
    assert "procedure(cadBaseInputRevision()" in base
    assert 'cadBaseInputRevision()=="20260716.input.sync.v1"' in entry

    assert "procedure(rceSyncInputNames(form source)" in callback
    assert "form~>layLib~>value=libName" in callback
    assert "form~>layCell~>value=cellName" in callback
    assert "form~>layView~>value=viewName" in callback
    assert "form~>schLib~>value=libName" in callback
    assert "form~>schCell~>value=cellName" in callback
    assert "form~>schView~>value=viewName" in callback
    assert "form->inputSync->invisible=(form->inpType->value!=\"OA\")" in callback
    assert "rceSyncExtractTopCell(form)" in callback
    assert "isCallable('rceSyncInputNames)" in entry


def test_lvs_oa_input_names_can_be_synchronized_in_either_direction() -> None:
    gui = read_skill_source(CAD_ROOT / "lvs/skill++/LVSGUI.ils")
    callback = read_skill_source(CAD_ROOT / "lvs/skill++/LVSCB.ils")
    entry = read_skill_source(CAD_ROOT / "lvs/skill++/LVS.ils")

    for slot in ("inputSync", "schToLay", "layToSch"):
        assert f"({slot}" in gui
    assert "defmethod(makeLvsInputNameSync ((inst LVSGUI))" in gui
    assert '?buttonText "->"' in gui
    assert '?buttonText "<-"' in gui
    assert 'Copy Schematic Library/Cell to Layout' in gui
    assert 'Copy Layout Library/Cell to Schematic' in gui
    assert 'lvsSyncInputNames(hiGetCurrentForm() \\"Schematic\\")' in gui
    assert 'lvsSyncInputNames(hiGetCurrentForm() \\"Layout\\")' in gui
    assert "inst->inputSync=makevbl('inputSync" in gui
    assert "list(list(inst->frontendInp 'stretch 1)" in gui
    assert "list(inst->backendInp 'stretch 1)" in gui

    assert "procedure(lvsSyncInputNames(form source)" in callback
    assert "form~>layLib~>value=libName" in callback
    assert "form~>layCell~>value=cellName" in callback
    assert "form~>schLib~>value=libName" in callback
    assert "form~>schCell~>value=cellName" in callback
    assert "form->inputSync->invisible=(form->inpType->value!=\"OA\")" in callback
    sync = _procedure_body(callback, "lvsSyncInputNames")
    assert "viewName=or(form~>layView~>value \"\")" in sync
    assert "form~>layView~>value=viewName" in sync
    assert "viewName=or(form~>schView~>value \"\")" in sync
    assert "form~>schView~>value=viewName" in sync

    revision = "20260817.input.sync.v1"
    assert 'cadBaseInputRevision()=="20260716.input.sync.v1"' in entry
    assert f'lvsInputSyncRevision()=="{revision}"' in entry
    assert f'lvsInputSyncGuiRevision()=="{revision}"' in entry
    assert "isCallable('lvsSyncInputNames)" in entry


def test_rce_native_oa_views_are_wired_to_gui_toml_and_completion() -> None:
    helper = read_skill_source(CAD_ROOT / "rce/skill/RCE_toml.il")
    config = read_skill_source(CAD_ROOT / "rce/skill++/RCECFG.ils")
    gui = read_skill_source(CAD_ROOT / "rce/skill++/RCEGUI.ils")
    callback = read_skill_source(CAD_ROOT / "rce/skill++/RCECB.ils")
    entry = read_skill_source(CAD_ROOT / "rce/skill++/RCE.ils")

    for slot in (
        "nativeViewLay",
        "viewKind",
        "viewKindFixed",
        "viewName",
        "viewTarget",
        "viewDeviceMap",
        "viewLayerMap",
    ):
        assert f"({slot}" in gui
    assert '?prompt   "View Type:"' in gui
    assert 'rceSyncNativeView hiGetCurrentForm()' in gui
    assert "inst->nativeViewLay->invisible=t" in gui
    assert gui.index("inst->viewKind=hiCreateComboField(") < gui.index(
        "inst->viewLayerMap=hiCreateFileSelectorField("
    )

    assert "procedure(rceNativeViewOutputP" in callback
    for output_type in ("view", "extview", "smartview", "calibreview", "starrcview"):
        assert f'"{output_type}"' in callback
    for label, name in (
        ("Smart View", "smart_view"),
        ("Extracted View", "av_extracted"),
        ("Calibre View", "calibre"),
        ("OpenAccess Parasitic View", "starrc"),
    ):
        assert f'("{label}" "{name}")' in callback
    assert "form~>nativeViewLay~>invisible=!visible" in callback
    assert "rceNativeViewSyncActive=t" in callback
    assert 'form~>viewKindFixed~>value=selectedKind' in callback
    assert 'form~>viewKind~>invisible=t' in callback
    assert 'form~>viewKindFixed~>invisible=!visible' in callback
    assert "form~>viewKind~>items=" not in callback
    assert "rceNativeViewSyncActive=nil" in callback
    assert "form~>viewTarget~>value=" in callback
    assert '("SCH+GDS"' in callback
    assert '("OA" "CDL+LAY")' in callback
    assert '("OA" "SCH+GDS")' in callback
    assert 'form~>nameSource~>value=="Layout"' in callback
    assert 'SICO_envValue("RCE_CALIBRE_VIEW_CELLMAP")' in callback
    assert 'SICO_envValue("RCE_STARRC_OA_DEVICE_MAP")' in callback
    assert 'SICO_envValue("RCE_STARRC_OA_LAYER_MAP")' in callback

    assert 'SICO_tomlWriteSection(outFile "extract.view")' in callback
    for key in (
        "kind",
        "library",
        "cell",
        "name",
        "layout_view",
        "cellmap_file",
        "device_mapping_file",
        "layer_mapping_file",
    ):
        assert f'SICO_tomlWriteString(outFile "{key}"' in callback
    assert "outPath=if(nativeView then" in callback
    assert "member(normalized" in helper
    assert all(f'"{value}"' in helper for value in (
        "view", "extview", "smartview", "calibreview", "starrcview"
    ))

    assert "procedure(rceNativeViewStamp" in callback
    assert "ddGetObjFiles(viewObj)" in callback
    assert "ddGetObjLastModify(fileObj)" in callback
    assert "oldStamp=rceNativeViewStamp" in callback
    assert "newStamp=rceNativeViewStamp" in callback
    assert "not(equal(oldStamp newStamp))" in callback
    assert "protected=errset(mgc_rve_load_setup_file(setupFile) t)" in callback
    assert "importResult=if(protected then t else nil)" in callback
    assert "refreshResult=errset(ddUpdateLibList() t)" in callback
    assert "procedure(rceCompleteViewRequest" in callback
    assert '("native" rceCreateNativeViewFromRequest(request launchLog))' in callback
    assert '?nativeViewImporter' in config
    assert "isCallable('mgc_rve_load_setup_file)" in callback

    for callable_name in (
        "rceNativeViewOutputP",
        "rceSyncNativeView",
        "rceCreateNativeViewFromRequest",
        "rceCompleteViewRequest",
    ):
        assert f"isCallable('{callable_name})" in entry
    assert "isCallable('RCE_tomlRevision)" in entry
    assert 'RCE_tomlRevision()=="20260803.multi.corner.v5"' in entry
    outer_guard = entry[: entry.index("procedure(rceLoaderRevision")]
    assert 'RCE_tomlRevision()=="20260803.multi.corner.v5"' in outer_guard
