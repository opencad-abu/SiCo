from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest


CAD_ROOT = Path(__file__).resolve().parents[3]
CORE = CAD_ROOT / "utility/skill/cadChangeTechLib.il"
VIA_CORE = CAD_ROOT / "utility/skill/cadChangeTechLibVia.il"
CALLBACKS = CAD_ROOT / "utility/skill/cadChangeTechLibGui.il"
VIA_CALLBACKS = CAD_ROOT / "utility/skill/cadChangeTechLibViaGui.il"
FORM = CAD_ROOT / "utility/skill/cadChangeTechLibForm.il"


def _parentheses_are_balanced(source: str) -> bool:
    depth = 0
    in_string = False
    escaped = False
    in_comment = False
    for character in source:
        if in_comment:
            if character == "\n":
                in_comment = False
            continue
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == ";":
            in_comment = True
        elif character == '"':
            in_string = True
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0 and not in_string


def test_core_exposes_four_argument_mapping_contract() -> None:
    source = CORE.read_text(encoding="utf-8")

    assert (
        "procedure(cadChangeTechLib(designLib designCell designView mappingfile)"
        in source
    )
    assert 'parseString(line " \\t\\r\\n")' in source
    assert "length(fields)==6" in source
    assert 'if(pattern=="*" then' in source
    assert 'rexMatchp(strcat("^" pattern "$") value)' in source
    assert "procedure(cadChangeTechLibPatternValidP(pattern)" in source
    assert "procedure(cadChangeTechLibMatchingCellNames(" in source
    assert "cell~>name designCellPattern" in source
    assert "procedure(cadChangeTechLibProcessCell(" in source
    assert "foreach(cellName cellNames" in source
    assert "ddGetObj(targetLib targetCell targetView)" in source
    assert "dbSetInstHeaderMasterName(instH targetLib targetCell targetView)" in source
    assert "procedure(cadChangeTechLibResolveHeader(cellView instH)" in source
    assert 'cellView~>cellViewType=="schematic"' in source
    assert "dbSave(cellView)" in source
    assert "dbClose(cellView)" in source


def test_via_core_uses_four_field_cross_technology_mappings() -> None:
    source = VIA_CORE.read_text(encoding="utf-8")

    assert "sourceLib sourceViaDef targetLib targetViaDef" in source
    assert "length(fields)==4" in source
    assert "procedure(cadChangeTechLibReadViaMappingFile(mappingfile)" in source
    assert "procedure(cadChangeTechLibWriteViaMappingFile(mappingfile mappings)" in source
    assert "procedure(cadChangeTechLibReplaceViaMasters(" in source
    assert "designLib designCell designView viaMappingFile)" in source
    assert "procedure(cadChangeTechLibReplaceViaMastersInCell(" in source
    assert "cadChangeTechLibViaMatchingCellNames(" in source
    assert "foreach(cellName cellNames" in source
    assert "cadChangeTechLibViaSourceLib(viaHeader)" in source
    assert "master~>libName" in source
    assert "techGetTechFileDdId(techId)" in source
    assert "techGetTechLibName(techDdId)" in source
    assert "cadChangeTechLibViaDefForLib(targetLib targetViaDef)" in source
    assert "viaObjects=foreach(mapcar item cellView~>vias item)" in source
    assert "dbCreateVia(cellView targetDef origin orient" in source
    assert "overrideParams=viaHeader~>overrideParams" in source
    assert "dbDeleteObject(via)" in source
    assert "dbSave(cellView)" in source
    assert "dbClose(cellView)" in source


def test_gui_builds_and_persists_six_field_mappings() -> None:
    callbacks = CALLBACKS.read_text(encoding="utf-8")
    form = FORM.read_text(encoding="utf-8")

    for field in (
        "cadCtlSourceLib",
        "cadCtlSourceCell",
        "cadCtlSourceView",
        "cadCtlTargetLib",
        "cadCtlTargetCell",
        "cadCtlTargetView",
    ):
        assert field in callbacks
        assert field in form
    assert "procedure(cadChangeTechLibGuiAddMappingCB(form)" in callbacks
    assert "procedure(cadChangeTechLibGuiUpdateMappingCB(form)" in callbacks
    assert "procedure(cadChangeTechLibGuiLoadMappingCB(form)" in callbacks
    assert "procedure(cadChangeTechLibGuiSaveMappingCB(form)" in callbacks
    assert "cadChangeTechLibWriteMappingFile(path form~>cadMappings)" in callbacks
    assert "procedure(cadChangeTechLibGuiCreateForm()" in form
    assert "ddHiCreateLibraryComboField(" in form
    assert "ddHiCreateCellComboField(" in form
    assert "ddHiCreateViewComboField(" in form
    assert "ddHiLinkFields(" in form
    assert "procedure(cadChangeTechLibGui()" in form
    assert '?name \'cadCtlAddButton ?buttonText "Add"' in form
    assert '?name \'cadCtlUpdateButton ?buttonText "Update"' in form


def test_layout_via_master_controls_are_dynamic_and_wired_to_apply() -> None:
    callbacks = CALLBACKS.read_text(encoding="utf-8")
    via_callbacks = VIA_CALLBACKS.read_text(encoding="utf-8")
    form = FORM.read_text(encoding="utf-8")

    assert "procedure(cadChangeTechLibGuiUpdateViaControls(form)" in via_callbacks
    assert "procedure(cadChangeTechLibGuiDesignViewCB(form)" in via_callbacks
    assert "procedure(cadChangeTechLibGuiViaToggleCB(form)" in via_callbacks
    assert "cadChangeTechLibReplaceViaMasters(" in callbacks
    assert "and(!form~>cadMappings !viaEnabled)" in callbacks
    assert "cadChangeTechLibWriteViaMappingFile(" in callbacks
    assert "viaPath form~>cadViaMappings" in callbacks
    assert "hiCreateBooleanButton(" in form
    assert "cadCtlUpdateViaMaster" in form
    for field in (
        "cadCtlViaSourceLib",
        "cadCtlViaSourceDef",
        "cadCtlViaTargetLib",
        "cadCtlViaTargetDef",
        "cadCtlViaMappingList",
        "cadCtlViaMappingFile",
    ):
        assert field in via_callbacks
        assert field in form
    assert "cadCtlOldViaMaster" not in form
    assert "cadCtlNewViaMaster" not in form
    assert "cadChangeTechLibGuiDesignViewCB(hiGetCurrentForm())" in form
    assert "hiCreateStackedLayout('cadChangeTechLibViaStackLayout" in form
    assert "?items list(viaEmptyLayout viaMasterLayout) ?currentIndex 1" in form
    assert "hiSetStackedLayoutCurrentIndex(" in via_callbacks
    assert "'cadChangeTechLibViaStackLayout" in via_callbacks
    assert "form~>cadCtlUpdateViaMaster~>invisible=!layoutP" in via_callbacks
    assert "form~>cadViaMasterLayout" not in via_callbacks
    assert "?items list(viaMappingEditorLayout)" in form
    assert "?items list(updateViaMaster viaMappingEditorLayout)" not in form
    assert "?items list(designLayout mappingLayout fileLayout updateViaMaster" in form
    assert "viaStackLayout status))" in form
    via_editor = form[
        form.index("viaMappingEditorLayout=hiCreateVerticalBoxLayout(") :
        form.index("viaMasterLayout=hiCreateVerticalBoxLayout(")
    ]
    assert "?invisible t" not in via_editor
    assert 'cadChangeTechLibFormVersion="20260902.form.lifecycle.v1"' in form


def test_via_def_combos_follow_selected_technology_library() -> None:
    callbacks = VIA_CALLBACKS.read_text(encoding="utf-8")
    form = FORM.read_text(encoding="utf-8")

    assert "procedure(cadChangeTechLibGuiViaDefNames(libName)" in callbacks
    assert "techId~>viaDefs~>name" in callbacks
    assert "procedure(cadChangeTechLibGuiViaSourceLibCB(form)" in callbacks
    assert "procedure(cadChangeTechLibGuiViaTargetLibCB(form)" in callbacks
    assert "form~>cadCtlViaSourceDef~>items=choices" in callbacks
    assert "form~>cadCtlViaTargetDef~>items=choices" in callbacks
    assert 'choices=append(names list("*"))' in callbacks
    assert "viaSourceDef=hiCreateComboField(" in form
    assert "viaTargetDef=hiCreateComboField(" in form
    assert "cadChangeTechLibGuiViaSourceLibCB(hiGetCurrentForm())" in form
    assert "cadChangeTechLibGuiViaTargetLibCB(hiGetCurrentForm())" in form


def test_form_callbacks_resolve_the_current_form_instead_of_the_global() -> None:
    form = FORM.read_text(encoding="utf-8")

    callback_names = (
        "cadChangeTechLibGuiDesignViewCB",
        "cadChangeTechLibGuiAddMappingCB",
        "cadChangeTechLibGuiUpdateMappingCB",
        "cadChangeTechLibGuiRemoveMappingCB",
        "cadChangeTechLibGuiClearMappingCB",
        "cadChangeTechLibGuiSelectMappingCB",
        "cadChangeTechLibGuiLoadMappingCB",
        "cadChangeTechLibGuiSaveMappingCB",
        "cadChangeTechLibGuiViaToggleCB",
        "cadChangeTechLibGuiViaSourceLibCB",
        "cadChangeTechLibGuiViaTargetLibCB",
        "cadChangeTechLibGuiViaAddMappingCB",
        "cadChangeTechLibGuiViaUpdateMappingCB",
        "cadChangeTechLibGuiViaRemoveMappingCB",
        "cadChangeTechLibGuiViaClearMappingCB",
        "cadChangeTechLibGuiViaSelectMappingCB",
        "cadChangeTechLibGuiViaLoadMappingCB",
        "cadChangeTechLibGuiViaSaveMappingCB",
    )
    for callback in callback_names:
        assert f"{callback}(hiGetCurrentForm())" in form
        assert f"{callback}(cadChangeTechLibForm)" not in form


def test_design_cell_and_source_via_def_support_patterns() -> None:
    core = CORE.read_text(encoding="utf-8")
    via_core = VIA_CORE.read_text(encoding="utf-8")
    form = FORM.read_text(encoding="utf-8")

    assert "?defValue defaultCell ?editable t" in form
    assert "cadChangeTechLibPatternValidP(designCell)" in core
    assert "cadChangeTechLibViaPatternValidP(designCell)" in via_core
    assert "cadChangeTechLibViaPatternValidP(sourceViaDef)" in via_core
    assert "viaDefName cadChangeTechLibViaRowField(row 1)" in via_core
    assert 'when(targetViaDef=="*"' in via_core
    assert "targetViaDef=sourceViaDef" in via_core


def test_gui_supports_multiple_via_mapping_rows() -> None:
    callbacks = VIA_CALLBACKS.read_text(encoding="utf-8")
    form = FORM.read_text(encoding="utf-8")

    assert "procedure(cadChangeTechLibGuiViaAddMappingCB(form)" in callbacks
    assert "procedure(cadChangeTechLibGuiViaUpdateMappingCB(form)" in callbacks
    assert "procedure(cadChangeTechLibGuiViaRemoveMappingCB(form)" in callbacks
    assert "procedure(cadChangeTechLibGuiViaClearMappingCB(form)" in callbacks
    assert "procedure(cadChangeTechLibGuiViaLoadMappingCB(form)" in callbacks
    assert "procedure(cadChangeTechLibGuiViaSaveMappingCB(form)" in callbacks
    assert "choices=foreach(mapcar row form~>cadViaMappings" in callbacks
    assert "rows=append1(rows row)" in callbacks
    assert "form~>cadViaMappings=cadChangeTechLibGuiReplaceAt(" in callbacks
    assert '?name \'cadCtlViaAddButton ?buttonText "Add"' in form
    assert '?name \'cadCtlViaUpdateButton ?buttonText "Update"' in form


def test_gui_formats_mapping_rows_and_updates_existing_rows() -> None:
    callbacks = CALLBACKS.read_text(encoding="utf-8")

    assert "choices=foreach(mapcar row form~>cadMappings" in callbacks
    assert (
        "result=append1(result if(count==index then replacement else item))"
        in callbacks
    )
    assert "rows=append1(rows row)" in callbacks
    assert "form~>cadMappings=cadChangeTechLibGuiReplaceAt(rows index row)" in callbacks


def test_change_tech_lib_skill_sources_are_balanced_and_guarded() -> None:
    for path, version, revision in (
        (CORE, "sicoChangeTechLibCoreVersion", "20260811."),
        (VIA_CORE, "cadChangeTechLibViaVersion", "20260814.via.v6"),
        (CALLBACKS, "sicoChangeTechLibGuiVersion", "20260902.form.lifecycle.v1"),
        (VIA_CALLBACKS, "sicoChangeTechLibViaGuiVersion", "20260811."),
        (FORM, "cadChangeTechLibFormVersion", "20260902.form.lifecycle.v1"),
    ):
        source = path.read_text(encoding="utf-8")
        assert f"boundp('{version})" in source
        assert f'{version}="{revision}' in source
        assert _parentheses_are_balanced(source)


def test_change_tech_lib_sources_do_not_use_nonlocal_return() -> None:
    for path in (CORE, VIA_CORE, CALLBACKS, VIA_CALLBACKS, FORM):
        assert "return(" not in path.read_text(encoding="utf-8")


def test_change_tech_lib_core_and_via_reload_without_redefinition(
    tmp_path: Path,
) -> None:
    dbaccess = shutil.which(os.environ.get("RCE_DBACCESS", "dbAccess"))
    if dbaccess is None:
        pytest.skip("dbAccess is unavailable")
    skill = "\n".join(
        (
            f'load("{VIA_CORE}")',
            f'load("{CORE}")',
            f'load("{VIA_CORE}")',
            'if(and(cadChangeTechLibCoreRevision()=="20260811.mapping.v7"',
            '       cadChangeTechLibViaRevision()=="20260814.via.v6"',
            "       isCallable('cadChangeTechLibMatchingCellNames)",
            "       isCallable('cadChangeTechLibViaMatchingCellNames))",
            '  then printf("CAD_CHANGE_TECH_LIB_RELOAD_OK\\n"))',
            "exit()",
        )
    )
    completed = subprocess.run(
        [dbaccess],
        input=skill,
        text=True,
        capture_output=True,
        cwd=tmp_path,
        timeout=40,
        check=False,
        env={name: value for name, value in os.environ.items() if name != "CAD_HOME"},
    )
    output = completed.stdout + completed.stderr

    assert completed.returncode == 0, output
    assert "*Error*" not in output
    assert "(reader)" not in output
    assert "still unclosed on EOF" not in output
    assert "function cadChangeTechLib" not in output
    assert "CAD_CHANGE_TECH_LIB_RELOAD_OK" in output
