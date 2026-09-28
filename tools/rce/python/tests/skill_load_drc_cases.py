from skill_load_fixtures import (
    CAD_ROOT,
    _procedure_body,
)
from skill_test_support import read_skill_source


def test_instance_section_label_and_default_are_correct() -> None:
    base = read_skill_source(CAD_ROOT / "rce/skill++/RCEBASEGUI.ils")
    start = base.index("defmethod(makeNlMoreInfo")
    end = base.index("\ndefmethod(", start + 1)
    method = base[start:end]

    assert '"- Instance Session:"' not in base
    assert '?prompt   "- Instance Section:"' in method
    assert '?value    "FALSE"' in method
    assert '?defValue "FALSE"' in method

    entry = read_skill_source(CAD_ROOT / "rce/skill++/RCE.ils")
    assert "isCallable('cadBaseGuiRevision)" in entry
    assert 'cadBaseGuiRevision()=="20260721.case.sensitive.v2"' in entry
    assert "isCallable('cadBaseMultiCornerRevision)" in entry
    assert (
        'cadBaseMultiCornerRevision()=="20260803.multi.corner.align.v4"'
        in entry
    )


def test_lvs_rule_environment_is_split_between_lvs_and_rce() -> None:
    env_options = read_skill_source(CAD_ROOT / "common/skill/SICO_envOptions.il")
    base = read_skill_source(CAD_ROOT / "common/skill++/BASEGUI.ils")
    lvs_entry = read_skill_source(CAD_ROOT / "lvs/skill++/LVS.ils")
    lvs_config = read_skill_source(CAD_ROOT / "lvs/skill++/LVSCFG.ils")
    lvs_gui = read_skill_source(CAD_ROOT / "lvs/skill++/LVSGUI.ils")
    lvs_callback = read_skill_source(CAD_ROOT / "lvs/skill++/LVSCB.ils")
    rce_config = read_skill_source(CAD_ROOT / "rce/skill++/RCECFG.ils")
    rce_gui = read_skill_source(CAD_ROOT / "rce/skill++/RCEGUI.ils")
    rce_callback = read_skill_source(CAD_ROOT / "rce/skill++/RCECB.ils")

    assert "CAD_envPreferredName" not in env_options
    assert "(lvsRunsetEnvName" in base
    assert "SICO_envPairValue(inst->lvsRunsetEnvName" in base
    assert "PROJ_LVS_RUNSET" not in base
    assert "RCE_LVS_FILE" not in base

    for text in (lvs_config, lvs_callback):
        assert '"LVS_FILE"' in text
        assert "PROJ_LVS_RUNSET" not in text
        assert "RCE_LVS_FILE" not in text
    for text in (rce_config, rce_callback):
        assert '"RCE_LVS_FILE"' in text
        assert "PROJ_LVS_RUNSET" not in text
        assert '"LVS_FILE"' not in text

    assert "gui->lvsRunsetEnvName     = lvsStruc->lvsRunsetEnvName" in lvs_gui
    assert "gui->lvsRunsetEnvName     = extstrStruc->lvsRunsetEnvName" in rce_gui
    assert "PRO_LVS_FILE" not in "".join(
        (env_options, base, lvs_config, lvs_callback, rce_config, rce_callback)
    )
    assert 'cadBaseGuiRevision()=="20260721.case.sensitive.v2"' in lvs_entry


def test_drc_rule_environment_uses_file_name() -> None:
    sources = tuple(
        read_skill_source(CAD_ROOT / relative)
        for relative in (
            "drc/skill++/DRCCFG.ils",
            "drc/skill++/DRCGUI.ils",
            "drc/skill++/DRCCB.ils",
        )
    )

    assert all("DRC_FILE" in source for source in sources)
    assert all("PROJ_DRC_RUNSET" not in source for source in sources)


def test_drc_rule_select_is_wired_to_gui_and_backend() -> None:
    gui = read_skill_source(CAD_ROOT / "drc/skill++/DRCGUI.ils")
    callback = read_skill_source(CAD_ROOT / "drc/skill++/DRCCB.ils")
    selector = read_skill_source(CAD_ROOT / "drc/skill++/DRCRULESEL.ils")
    entry = read_skill_source(CAD_ROOT / "drc/skill++/DRC.ils")
    protocol = read_skill_source(CAD_ROOT / "common/skill/SICO_guiProtocol.il")
    generator = (CAD_ROOT / "drc/python/drcpy/generator.py").read_text(
        encoding="utf-8"
    )

    assert "makeDrcRuleSelect(inst)" in gui
    assert '?buttonText     "Rule Select"' in gui
    assert '?callback "drcRuleSelectInvalidate(hiGetCurrentForm())"' in gui
    assert "drcRuleSelectBind(gui->mainForm)" in gui
    assert 'SICO_tomlWriteBool(outFile "rule_select_enable"' in callback
    assert 'SICO_tomlWriteStringList(outFile "rule_select_groups"' in callback
    assert 'SICO_tomlWriteStringList(outFile "rule_select_checks"' in callback
    assert 'strcat(drcSkillRoot "/skill++/DRCRULESEL.ils")' in entry
    assert '" rule-select-gui "' in selector
    assert 'SICO_tempPath("drc-rule-select-inputXXXX")' in selector
    assert 'SICO_tempPath("drc-rule-select-resultXXXX")' in selector
    assert "drcRuleSelectWriteInitial" in selector
    assert "drcRuleSelectReadResult" in selector
    assert "SICO_guiPythonLaunchCommand" in selector
    assert "exec env -u PYTHONHOME -u PYTHONPATH" in protocol
    assert "-u QT_QPA_PLATFORM_PLUGIN_PATH" in protocol
    assert 'strcat(drcCommonRoot "/skill/SICO_guiProtocol.il")' in entry
    assert "--parent-pid" in selector
    assert "drcRuleSelectRequestToken" in selector
    assert "hiIsFormDisplayed(form)" in selector
    assert "ipcKillProcess" in selector
    assert "DRC SELECT CHECK" in generator


def test_drc_and_lvs_advanced_options_are_grouped_under_disclosures() -> None:
    drc_gui = read_skill_source(CAD_ROOT / "drc/skill++/DRCGUI.ils")
    lvs_gui = read_skill_source(CAD_ROOT / "lvs/skill++/LVSGUI.ils")
    drc_entry = read_skill_source(CAD_ROOT / "drc/skill++/DRC.ils")
    lvs_entry = read_skill_source(CAD_ROOT / "lvs/skill++/LVS.ils")

    assert (
        "'drcMoreContents list(inst->drcRuleSelect inst->customSvrf)" in drc_gui
    )
    assert '?labelText   "More DRC Options"' in drc_gui
    assert "?onFields    '(drcMoreContents)" in drc_gui
    assert "'inputMoreContents list(inst->cdlInclude" in lvs_gui
    assert '?labelText   "More Input Options"' in lvs_gui
    assert "?onFields    '(inputMoreContents)" in lvs_gui
    assert "'lvsMoreContents list(inst->svdbQuery inst->lvsRecognizeGates inst->customSvrf)" in lvs_gui
    assert '?labelText   "More LVS Options"' in lvs_gui
    assert "?onFields    '(lvsMoreContents)" in lvs_gui
    assert (
        'drcAdvancedOptionsGuiRevision()=="20260817.advanced.options.v1"'
        in drc_entry
    )
    assert (
        'lvsAdvancedOptionsGuiRevision()=="20260817.advanced.options.v1"'
        in lvs_entry
    )


def test_drc_lvs_and_rce_show_rule_file_label() -> None:
    revision = "20260812.rule.file.label.v1"
    base = read_skill_source(CAD_ROOT / "common/skill++/BASEGUI.ils")
    drc_gui = read_skill_source(CAD_ROOT / "drc/skill++/DRCGUI.ils")

    for text, method in (
        (base, "makeLvsRunset"),
        (drc_gui, "makeDrcRunset"),
    ):
        start = text.index(f"defmethod({method}")
        end = text.index("\ndefmethod(", start + 1)
        body = text[start:end]
        assert '?prompt   "Rule File:"' in body
        assert "Runset" not in body.split("?prompt", 1)[1].split("?items", 1)[0]

    assert f'cadBaseRuleFileLabelRevision()=="{revision}"' in read_skill_source(CAD_ROOT / "lvs/skill++/LVS.ils")
    assert f'cadBaseRuleFileLabelRevision()=="{revision}"' in read_skill_source(CAD_ROOT / "rce/skill++/RCE.ils")
    assert f'drcRuleFileGuiRevision()=="{revision}"' in read_skill_source(CAD_ROOT / "drc/skill++/DRC.ils")


def test_drc_and_lvs_run_modes_and_rve_completion_are_wired() -> None:
    drc_gui = read_skill_source(CAD_ROOT / "drc/skill++/DRCGUI.ils")
    drc_callback = read_skill_source(CAD_ROOT / "drc/skill++/DRCCB.ils")
    drc_run = read_skill_source(CAD_ROOT / "drc/skill++/DRCRUN.ils")
    lvs_gui = read_skill_source(CAD_ROOT / "lvs/skill++/LVSGUI.ils")
    lvs_callback = read_skill_source(CAD_ROOT / "lvs/skill++/LVSCB.ils")
    lvs_run = read_skill_source(CAD_ROOT / "lvs/skill++/LVSRUN.ils")

    for gui, field, callback, form in (
        (drc_gui, "drcRunMode", "drcRunModeCB", "drcForm"),
        (lvs_gui, "lvsRunMode", "lvsRunModeCB", "lvsForm"),
    ):
        assert f"?name        '{field}" in gui
        assert '?prompt      "Run Mode:"' in gui
        assert "?choices     '(\"Hier\" \"Flat\")" in gui
        assert '?defValue    "Hier"' in gui
        assert (
            f'?callback    \'("({callback} hiGetCurrentForm())")' in gui
            or f'?callback    \'("{callback}(hiGetCurrentForm())")' in gui
        )

    assert "form~>runCpu~>value=\"1\"" in drc_callback
    assert "form~>runCpu~>enabled=hier" in drc_callback
    assert 'SICO_tomlWriteString(outFile "run_mode" form~>drcRunMode~>value)' in drc_callback
    assert "form~>lvsCpu~>value=\"1\"" in lvs_callback
    assert "form~>lvsCpu~>enabled=hier" in lvs_callback
    assert "form~>lvsHcellBtn~>value=nil" in lvs_callback
    assert "form~>lvsHcellBtn~>enabled=hier" in lvs_callback
    assert 'SICO_tomlWriteString(outFile "run_mode" form~>lvsRunMode~>value)' in lvs_callback

    drc_post = _procedure_body(drc_run, "drcIpcPostFunc")
    assert "drcPromptOpenRve(config resultsDb)" in drc_post
    assert '"/db/drc/cal_drc.out"' in drc_run
    assert 'strcat("Open Calibre RVE?\\n\\ncalibre -rve " resultsDb)' in drc_run
    assert 'SICO_backendCommand("drc" DRC_pythonExe() DRC_pythonEntry()) " report "' in drc_run
    assert "drcIpcTable[cid]=list(pid launchLog cmdFile" in drc_run

    lvs_post = _procedure_body(lvs_run, "lvsIpcPostFunc")
    assert "lvsShowCompletionSummary(config svdb exitStatus)" in lvs_post
    assert "lvsPromptOpenRve(config svdb)" not in lvs_post
    assert "LVS Status: CORRECT :-)" in lvs_run
    assert "LVS Status: INCORRECT :-(" in lvs_run
    assert 'strcat(runDir "/log/callvs.log")' in lvs_run
    assert 'rexMatchp("LVS completed\\\\. CORRECT\\\\." line)' in lvs_run
    assert '?buttons       \'("Open Calibre RVE" "Close")' in lvs_run
    assert "procedure(lvsOpenRve(config svdb)" in lvs_run
    assert 'strcat("Open Calibre RVE?\\n\\ncalibre -rve " svdb)' in lvs_run
    assert 'return(lvsOpenRve(config svdb))' in lvs_run

    lvs_loader = read_skill_source(CAD_ROOT / "lvs/skill++/LVS.ils")
    loader_guard = lvs_loader.split("procedure(lvsLoaderRevision", 1)[0]
    assert 'lvsRveLauncherRevision()=="20260828.prompt.return.v1"' in loader_guard
    assert "isCallable('lvsOpenRve)" in loader_guard
    assert "isCallable('lvsShowCompletionSummary)" in loader_guard
