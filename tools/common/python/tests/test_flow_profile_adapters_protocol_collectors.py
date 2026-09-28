from __future__ import annotations

from flow_profile_adapter_fixtures import *

@pytest.mark.parametrize(
    (
        "flow",
        "loader_path",
        "adapter_file",
        "gui_file",
        "revision",
        "adapter_revision",
    ),
    (
        (
            "DRC",
            "drc/skill++/DRC.ils",
            "DRCPROFILE.ils",
            "DRCGUI.ils",
            "drcProfileRevision",
            ADAPTER_PROFILE_REVISION,
        ),
        (
            "LVS",
            "lvs/skill++/LVS.ils",
            "LVSPROFILE.ils",
            "LVSGUI.ils",
            "lvsProfileRevision",
            ADAPTER_PROFILE_REVISION,
        ),
        ("RCE", "rce/skill++/RCE.ils", "RCEPROFILE.ils", "RCEGUI.ils", "rceProfileRevision", RCE_PROFILE_REVISION),
    ),
)
def test_loaders_gate_and_load_profile_dependencies_before_gui(
    flow: str,
    loader_path: str,
    adapter_file: str,
    gui_file: str,
    revision: str,
    adapter_revision: str,
) -> None:
    loader = _source(loader_path)

    _assert_order(
        loader,
        '"/skill/SICO_toml.il"',
        '"/skill/SICO_profile.il"',
        '"/skill++/PROFILEGUI.ils"',
        f'"/skill++/{adapter_file}"',
        f'"/skill++/{gui_file}"',
    )
    assert loader.count("isCallable('SICO_profileRevision)") == 2
    assert loader.count(f'SICO_profileRevision()=="{PROFILE_REVISION}"') == 2
    assert loader.count("isCallable('SICO_profileGuiRevision)") == 2
    assert loader.count(f'SICO_profileGuiRevision()=="{PROFILE_GUI_REVISION}"') == 2
    assert loader.count(f"isCallable('{revision})") == 2
    assert loader.count(f'{revision}()=="{adapter_revision}"') == 2
    assert loader.count(f'SICO_profileAdapter("{flow}")') == 2


@pytest.mark.parametrize(
    ("flow", "relative", "revision", "marker"),
    (
        ("DRC", "drc/skill++/DRCPROFILE.ils", "drcProfileRevision", "DRC_PROFILE_OK"),
        ("LVS", "lvs/skill++/LVSPROFILE.ils", "lvsProfileRevision", "LVS_PROFILE_OK"),
        ("RCE", "rce/skill++/RCEPROFILE.ils", "rceProfileRevision", "RCE_PROFILE_OK"),
    ),
)
def test_profile_adapters_are_reader_clean_with_dbaccess(
    flow: str, relative: str, revision: str, marker: str
) -> None:
    dbaccess = shutil.which(os.environ.get("RCE_DBACCESS", "dbAccess"))
    if dbaccess is None:
        pytest.skip("dbAccess is unavailable")
    sources = (
        CAD_ROOT / "common/skill/SICO_toml.il",
        CAD_ROOT / "common/skill/SICO_profile.il",
        CAD_ROOT / relative,
    )
    skill = "\n".join(
        (
            *(f'load("{source}")' for source in sources),
            f"if(and(isCallable('{revision}) SICO_profileAdapter(\"{flow}\"))",
            f'  then printf("{marker}\\n"))',
            "exit()",
        )
    )
    completed = subprocess.run(
        [dbaccess],
        input=skill,
        text=True,
        capture_output=True,
        timeout=40,
        check=False,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "*Error*" not in output
    assert "(reader)" not in output
    assert "still unclosed on EOF" not in output
    assert marker in output


def test_shared_profile_apply_rolls_back_failed_update_with_dbaccess() -> None:
    dbaccess = shutil.which(os.environ.get("RCE_DBACCESS", "dbAccess"))
    if dbaccess is None:
        pytest.skip("dbAccess is unavailable")
    sources = (
        CAD_ROOT / "common/skill/SICO_toml.il",
        CAD_ROOT / "common/skill/SICO_profile.il",
    )
    skill = "\n".join(
        (
            "defstruct(profileTransactionProbe value)",
            *(f'load("{source}")' for source in sources),
            "procedure(profileTransactionCollect(form) "
            'list(list("value" form~>value)))',
            "procedure(profileTransactionApply(form data) "
            'form~>value=SICO_profileDataValue(data "value" "") '
            'form~>value!="FAIL")',
            "SICO_profileRegisterAdapter(\"DRC\" "
            "'profileTransactionCollect 'profileTransactionApply)",
            "form=make_profileTransactionProbe(?value \"BEFORE\")",
            "applyOk=SICO_profileApply(form \"DRC\" "
            'list(list("value" "FAIL")))',
            'when(and(!applyOk form~>value=="BEFORE")',
            '  printf("PROFILE_ROLLBACK_OK\\n"))',
            "exit()",
        )
    )
    completed = subprocess.run(
        [dbaccess],
        input=skill,
        text=True,
        capture_output=True,
        timeout=40,
        check=False,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "*Error*" not in output
    assert "(reader)" not in output
    assert "PROFILE_ROLLBACK_OK" in output


def test_drc_lvs_collectors_omit_empty_arrays_with_dbaccess() -> None:
    dbaccess = shutil.which(os.environ.get("RCE_DBACCESS", "dbAccess"))
    if dbaccess is None:
        pytest.skip("dbAccess is unavailable")
    sources = (
        CAD_ROOT / "common/skill/SICO_toml.il",
        CAD_ROOT / "common/skill/SICO_profile.il",
        CAD_ROOT / "drc/skill++/DRCPROFILE.ils",
        CAD_ROOT / "lvs/skill++/LVSPROFILE.ils",
    )
    skill = "\n".join(
            (
                "defstruct(profileProbeField value)",
                'procedure(SICO_lsfSelectedHost(host) '
                'if(host=="Auto (LSF Scheduler)" then "" else host))',
                "defstruct(profileProbeForm outDirType outDirPath runType queueName "
            "srvName inpType cdlIncludeBtn schLib schCell schView cdlIncludeFile "
            "layLib layCell layView cdlFile cdlCell gdsFile gdsCell drcTool "
            "drcRunsetName drcRunsetFile drcRunMode drcRuleSelectEnable "
            "drcRuleSelectGroups drcRuleSelectChecks lvsTool lvsRunsetName "
            "lvsRunsetFile lvsRunMode lvsHcellBtn lvsHcellFile lvsIgnoreError "
            "lvsCaseBtn lvsRecognizeGates lvsVirtualConn lvsVirtualConnName customSvrfEnable "
            "customSvrfCommand runCpu lvsCpu batchRunScope batchParallelCells "
            "cadBatchTasks)",
            *(f'load("{source}")' for source in sources),
            "drcForm=make_profileProbeForm("
            '?outDirType make_profileProbeField(?value "Customize Directory") '
            '?outDirPath make_profileProbeField(?value "/runs") '
            '?runType make_profileProbeField(?value "Current Host") '
            '?queueName make_profileProbeField(?value "") '
            '?srvName make_profileProbeField(?value "") '
            '?layLib make_profileProbeField(?value "work") '
            '?layCell make_profileProbeField(?value "top") '
            '?layView make_profileProbeField(?value "layout") '
            '?drcTool make_profileProbeField(?value "Calibre") '
            '?drcRunsetName make_profileProbeField(?value "default") '
            '?drcRunsetFile make_profileProbeField(?value "/pdk/drc.rule") '
            '?drcRunMode make_profileProbeField(?value "Hier") '
            '?drcRuleSelectEnable make_profileProbeField(?value nil) '
            '?customSvrfEnable make_profileProbeField(?value nil) '
            '?customSvrfCommand make_profileProbeField(?value "") '
            '?runCpu make_profileProbeField(?value "1") '
            '?batchRunScope make_profileProbeField(?value "Single Cell") '
            '?batchParallelCells make_profileProbeField(?value "2") '
            '?drcRuleSelectGroups nil ?drcRuleSelectChecks nil '
            '?cadBatchTasks nil)',
            "drcEmpty=drcProfileCollect(drcForm)",
            'drcEmptyOk=and(!assoc("drc.rule_select_groups" drcEmpty) '
            '!assoc("drc.rule_select_checks" drcEmpty) '
            '!assoc("batch.tasks" drcEmpty) '
            'equal(assoc("drc.rule_select_enable" drcEmpty) '
            'list("drc.rule_select_enable" nil)))',
            'drcForm~>drcRuleSelectGroups=list("group1") '
            'drcForm~>drcRuleSelectChecks=list("check1") '
            'drcForm~>cadBatchTasks=list(list("lib" "cell" "layout"))',
            "drcFull=drcProfileCollect(drcForm)",
            'drcFullOk=and(equal(SICO_profileDataValue(drcFull '
            '"drc.rule_select_groups") list("group1")) '
            'equal(SICO_profileDataValue(drcFull "drc.rule_select_checks") '
            'list("check1")) equal(SICO_profileDataValue(drcFull "batch.tasks") '
            'list(list("lib" "cell" "layout"))))',
            "lvsForm=make_profileProbeForm("
            '?outDirType make_profileProbeField(?value "Customize Directory") '
            '?outDirPath make_profileProbeField(?value "/runs") '
            '?runType make_profileProbeField(?value "Current Host") '
            '?queueName make_profileProbeField(?value "") '
            '?srvName make_profileProbeField(?value "") '
            '?inpType make_profileProbeField(?value "OA") '
            '?cdlIncludeBtn make_profileProbeField(?value nil) '
            '?schLib make_profileProbeField(?value "work") '
            '?schCell make_profileProbeField(?value "top") '
            '?schView make_profileProbeField(?value "schematic") '
            '?cdlIncludeFile make_profileProbeField(?value "") '
            '?layLib make_profileProbeField(?value "work") '
            '?layCell make_profileProbeField(?value "top") '
            '?layView make_profileProbeField(?value "layout") '
            '?cdlFile make_profileProbeField(?value "") '
            '?cdlCell make_profileProbeField(?value "") '
            '?gdsFile make_profileProbeField(?value "") '
            '?gdsCell make_profileProbeField(?value "") '
            '?lvsTool make_profileProbeField(?value "Calibre") '
            '?lvsRunsetName make_profileProbeField(?value "default") '
            '?lvsRunsetFile make_profileProbeField(?value "/pdk/lvs.rule") '
            '?lvsRunMode make_profileProbeField(?value "Hier") '
            '?lvsHcellBtn make_profileProbeField(?value nil) '
            '?lvsHcellFile make_profileProbeField(?value "") '
            '?lvsIgnoreError make_profileProbeField(?value nil) '
            '?lvsCaseBtn make_profileProbeField(?value t) '
            '?lvsRecognizeGates make_profileProbeField(?value "NONE") '
            '?lvsVirtualConn make_profileProbeField(?value list(nil t)) '
            '?lvsVirtualConnName make_profileProbeField(?value "?") '
            '?customSvrfEnable make_profileProbeField(?value nil) '
            '?customSvrfCommand make_profileProbeField(?value "") '
            '?lvsCpu make_profileProbeField(?value "1") '
            '?batchRunScope make_profileProbeField(?value "Single Cell") '
            '?batchParallelCells make_profileProbeField(?value "2") '
            '?cadBatchTasks nil)',
            "lvsEmpty=lvsProfileCollect(lvsForm)",
            'lvsEmptyOk=and(!assoc("batch.tasks" lvsEmpty) '
            'equal(assoc("input.cdl_include_enable" lvsEmpty) '
            'list("input.cdl_include_enable" nil)))',
            'lvsForm~>cadBatchTasks=list(list("slib" "scell" "schematic" '
            '"llib" "lcell" "layout"))',
            "lvsFull=lvsProfileCollect(lvsForm)",
            'lvsFullOk=equal(SICO_profileDataValue(lvsFull "batch.tasks") '
            'list(list("slib" "scell" "schematic" "llib" "lcell" '
            '"layout")))',
            "when(and(drcEmptyOk drcFullOk lvsEmptyOk lvsFullOk)",
            '  printf("DRC_LVS_PROFILE_ARRAYS_OK\\n"))',
            "exit()",
        )
    )
    completed = subprocess.run(
        [dbaccess],
        input=skill,
        text=True,
        capture_output=True,
        timeout=40,
        check=False,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "*Error*" not in output
    assert "DRC_LVS_PROFILE_ARRAYS_OK" in output
