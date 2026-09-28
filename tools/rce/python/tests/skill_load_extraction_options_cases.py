import re

from skill_load_fixtures import (
    CAD_ROOT,
)
from skill_test_support import read_skill_source


def test_rce_netlist_customize_controls_follow_tool_and_output_support() -> None:
    base = read_skill_source(CAD_ROOT / "rce/skill++/RCEBASEGUI.ils")
    config = read_skill_source(CAD_ROOT / "rce/skill++/RCECFG.ils")
    gui = read_skill_source(CAD_ROOT / "rce/skill++/RCEGUI.ils")
    callback = read_skill_source(CAD_ROOT / "rce/skill++/RCECB.ils")
    entry = read_skill_source(CAD_ROOT / "rce/skill++/RCE.ils")

    assert '"Hierarchy Delimeter:"' not in base
    assert '?buttonText     "Hierarchy Delimiter:"' in base
    assert "?items    '(\"/\" \".\")" in base
    assert "?items    '(\"/\" \".\" \"?\")" not in base
    assert "form~>pinOrderChoices" not in callback
    assert "procedure(rceValidatePinOrder(form)" in callback
    assert "rceSyncPinOrder(form pinSupported)" in callback

    assert "procedure(rceSyncNetlistCustomize" in callback
    assert "procedure(rceNameSourceCB" in callback
    assert 'qrcView=and(viewOutput tool=="QRC")' in callback
    assert "form~>nameSource~>enabled=!qrcView" in callback
    assert "rceSyncNativeView(form)" in callback
    assert 'tool=="QRC" lowerCase(form~>nameSource~>value)=="layout"' in callback
    assert 'tool!="StarRC"' in callback
    assert "'(\"dspf\" \"spf\")" in callback
    assert 'tool=="CalXRC"' in callback
    assert "'(\"Cg+Cc\" \"Cg\")" in callback
    assert "not(capacitanceOnly)" in callback
    assert 'noParasitics=and(tool=="CalXRC" form~>rcType~>value=="NONE")' in callback
    assert "form~>netSelBtn~>enabled=!noParasitics" in callback
    assert "form~>relFilterCapBtn~>enabled=!noParasitics" in callback
    assert "form~>absFilterCapBtn~>enabled=!noParasitics" in callback
    assert "form~>absFilterResBtn~>enabled=!noParasitics" in callback
    assert 'form~>nlRmInst~>value="FALSE"' in callback
    assert 'form~>nlRmInst~>enabled=and(outputType=="dspf" !noParasitics)' in callback
    assert "form~>pinOrderBtn~>value=nil" in callback
    assert 'list("CDL Netlist File" "User Defined File")' in callback
    assert 'form~>pinOrderFile~>value=""' in callback
    assert "form~>hierDeliBtn~>value=nil" in callback
    assert "form~>nlLay~>invisible=viewOutput" in callback
    assert "form~>bracketBtn~>value=nil" in callback

    assert "list('parasiticCoordinates \"R&C Coordinates\")" in base
    assert "list('parasiticResLayer \"Parasitic R Layer Name\")" in base
    assert "list('parasiticResDimensions \"Parasitic R Size (W&L)\")" in base
    assert "(Parasitic\\ R\\ Metal\\ Name)" not in base
    assert "(Metal\\ Size\\(W\\&L\\))" not in base
    assert base.count("?value    '(nil nil nil)") >= 1
    assert base.count("?defValue '(nil nil nil)") >= 1
    assert "?callback inst->nlParInfoCB" in base
    assert "form~>nlMoreInfo~>value" not in callback
    for index, option in enumerate(
        (
            "parasitic_coordinates",
            "parasitic_res_layer",
            "parasitic_res_dimensions",
        )
    ):
        assert (
            f'SICO_tomlWriteBool(outFile "{option}" '
            f"nth({index} form~>nlParInfo~>value))"
        ) in callback

    assert "procedure(rceParasiticInfoCB" in callback
    assert 'form~>extTool~>value=="StarRC"' in callback
    assert "form~>nlParInfo~>parasiticCoordinates~>enabled" in callback
    assert "form~>nlParInfo~>parasiticResLayer~>enabled" in callback
    assert "form~>nlParInfo~>parasiticResDimensions~>enabled" in callback
    assert "?nlParInfoCB" in config
    assert "gui->nlParInfoCB          = extstrStruc->nlParInfoCB" in gui
    assert "isCallable('rceParasiticInfoCB)" in entry

    assert callback.count("rceSyncNetlistCustomize(form)") >= 8
    assert "?nameSourceCB" in config
    assert "gui->nameSourceCB" in gui
    assert "rceSyncNetlistCustomize(gui->mainForm)" in gui
    assert "isCallable('rceSyncNetlistCustomize)" in entry
    assert "isCallable('rceNameSourceCB)" in entry


def test_rce_process_corners_are_scanned_and_preserve_case() -> None:
    base = read_skill_source(CAD_ROOT / "rce/skill++/RCEBASEGUI.ils")
    config = read_skill_source(CAD_ROOT / "rce/skill++/RCECFG.ils")
    gui = read_skill_source(CAD_ROOT / "rce/skill++/RCEGUI.ils")
    callback = read_skill_source(CAD_ROOT / "rce/skill++/RCECB.ils")
    entry = read_skill_source(CAD_ROOT / "rce/skill++/RCE.ils")

    assert re.search(r'\?prompt\s+"Select\s+Corner:"', base)
    assert "inst->corner=hiCreateComboField(" in base
    assert "?items    inst->cornerChoices" in base
    assert "?editable nil" in base
    assert re.search(r'\?prompt\s+"Corner\s+Scope:"', base)
    assert "inst->cornerType=hiCreateRadioField(" in base
    assert "inst->cornerTypeChoices" in base
    assert 'rceBaseGuiRevision()=="20260909.pin.order.v1"' in entry
    assert "hiCreateToggleField(" in base
    assert '?choices     list(list(selectItemName ""))' in base
    assert "?scrollable  t" in base
    assert "inst->singleCornerSetup" in base
    assert "inst->multiCornerSetup" in base
    assert "multiCornerLabel" not in base
    assert re.search(r'\?prompt\s+"Model\s+Setup:"', base)
    assert re.search(r'\?prompt\s+"Extract\s+Type:"', base)
    assert "list('stretch_item 1)" in base
    assert "?callback inst->rcTechDirPathCB" in base
    assert "?cornerChoices        '(\"\")" in config
    assert "rceRcTechDirPathCB hiGetCurrentForm()" in config
    assert "rceCornerCB hiGetCurrentForm()" in config
    assert '"Single Corner" "Multiple Corners"' in config

    assert "procedure(rceProcessCornerNames" in callback
    for marker in ("qrcTechFile", "qrc.tch", "cap_coeff.dat", "xrc.cal", "nxtgrd"):
        assert marker in callback
    assert "form~>corner~>items=corners" in callback
    assert "form~>corner~>enabled=and(selected strlen(selected)>0)" in callback
    assert "procedure(rceSelectedProcessCorners" in callback
    assert "procedure(rceSelectedCornerTemperatures" in callback
    assert "procedure(rceCornerTypeCB" in callback
    assert "corner=nameField~>value" in callback
    assert "nameField~>_labelOnly=corner" in callback
    assert "protected=errset(getDirFiles(dir))" in callback
    assert callback.count("rceRefreshProcessCorners(form)") >= 3
    assert "rceRefreshProcessCorners(gui->mainForm)" in gui
    assert "procedure(rceBindCornerRows(form capacity)" in gui
    assert "rceBindCornerRows(gui->mainForm length(gui->cornerRowNames))" in gui
    assert '?widgetWidth 100' in gui
    assert '?widgetWidth 80' in gui
    assert 'SICO_tomlWriteString(outFile "corner" processCorner)' in callback
    assert 'SICO_tomlWriteStringList(outFile "corners" processCorners)' in callback
    assert (
        'SICO_tomlWriteStringList(outFile "corner_temperatures" cornerTemperatures)'
        in callback
    )
    assert "procedure(rceExpectedOutputPaths" in callback
    assert "Process Croner:" not in base
    assert "rceCronerCB" not in callback

    revisions = (
        (base, "rceBaseGuiRevision", "defmethod(makeNlOpt"),
        (config, "rceConfigRevision", "extmsgStruc=make_extmsg("),
        (callback, "rceFrontendRevision", "procedure(rcePrint"),
        (gui, "rceGuiRevision", "procedure(cadDisplayRceForm"),
    )
    for text, revision, final_definition in revisions:
        assert text.index(f"procedure({revision}(") > text.index(final_definition)
    for revision in ("rceConfigRevision", "rceFrontendRevision", "rceGuiRevision"):
        assert f"{revision}()==rceLoaderRevision()" in entry
    assert 'cadBaseGuiRevision()=="20260721.case.sensitive.v2"' in entry
