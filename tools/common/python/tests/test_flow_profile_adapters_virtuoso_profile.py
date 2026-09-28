from __future__ import annotations

from flow_profile_adapter_fixtures import *

@pytest.mark.skipif(not RUN_SKILL_PROBE, reason="set RCE_RUN_SKILL_PROBE=1")
@pytest.mark.parametrize(
    ("flow", "entry", "display", "form_name"),
    (
        ("DRC", "drc/skill++/DRC.ils", "cadDisplayDrcForm", "drcForm"),
        ("LVS", "lvs/skill++/LVS.ils", "cadDisplayLvsForm", "lvsForm"),
    ),
)
def test_drc_lvs_profiles_save_and_load_with_virtuoso(
    tmp_path: Path,
    flow: str,
    entry: str,
    display: str,
    form_name: str,
) -> None:
    virtuoso = shutil.which(os.environ.get("RCE_VIRTUOSO", "virtuoso"))
    if virtuoso is None:
        pytest.skip("virtuoso is unavailable")
    install = tmp_path / "install"
    install.mkdir()
    (install / "tools").symlink_to(CAD_ROOT, target_is_directory=True)
    profile = tmp_path / f"{flow.lower()}-profile.toml"
    replay = tmp_path / f"{flow.lower()}-profile.il"
    log = tmp_path / f"{flow.lower()}-profile.log"
    if flow == "DRC":
        set_state = (
            'form~>layLib~>value="profileLib"',
            'form~>layCell~>value="profileCell"',
            'form~>layView~>value="profileLayout"',
            'form~>drcRunsetFile~>value="rules.drc"',
            'form~>drcRunMode~>value="Hier"',
            'form~>runCpu~>value="4"',
            "drcRunModeCB(form)",
            "form~>drcRuleSelectEnable~>value=t",
            'form~>drcRuleSelectGroups=list("group1")',
            'form~>drcRuleSelectChecks=list("check1")',
            'form~>drcRuleSelectRuleFile=SICO_absolutePath('
            "form~>drcRunsetFile~>value)",
            "drcRuleSelectEnableCB(form)",
            "form~>customSvrfEnable~>value=t",
            'form~>customSvrfCommand~>value="DRC CHECK MAP"',
            "cadCustomSvrfCB(form)",
            'form~>cadBatchTasks=list(list("batchLib" "batchCell" "layout"))',
        )
        mutate_state = (
            'form~>layLib~>value="changed"',
            'form~>layCell~>value="changed"',
            'form~>drcRunsetFile~>value=""',
            'form~>runCpu~>value="1"',
            "form~>drcRuleSelectEnable~>value=nil",
            "form~>drcRuleSelectGroups=nil",
            "form~>drcRuleSelectChecks=nil",
            "form~>customSvrfEnable~>value=nil",
            'form~>customSvrfCommand~>value=""',
            "form~>cadBatchTasks=nil",
        )
        restored = (
            'form~>layLib~>value=="profileLib"',
            'form~>layCell~>value=="profileCell"',
            'form~>layView~>value=="profileLayout"',
            f'form~>drcRunsetFile~>value=="{tmp_path}/rules.drc"',
            'form~>runCpu~>value=="4"',
            "form~>drcRuleSelectEnable~>value",
            'equal(form~>drcRuleSelectGroups list("group1"))',
            'equal(form~>drcRuleSelectChecks list("check1"))',
            "form~>customSvrfEnable~>value",
            'form~>customSvrfCommand~>value=="DRC CHECK MAP"',
            'equal(form~>cadBatchTasks '
            'list(list("batchLib" "batchCell" "layout")))',
        )
    else:
        set_state = (
            'form~>inpType~>value="OA"',
            'form~>schLib~>value="profileLib"',
            'form~>schCell~>value="profileCell"',
            'form~>schView~>value="schematic"',
            'form~>layLib~>value="profileLib"',
            'form~>layCell~>value="profileCell"',
            'form~>layView~>value="layout"',
            'form~>lvsRunsetFile~>value="rules.lvs"',
            "form~>cdlIncludeBtn~>value=t",
            'form~>cdlIncludeFile~>value="header.cdl"',
            "cadCdlIncludeCB(form)",
            'form~>lvsRunMode~>value="Hier"',
            'form~>lvsCpu~>value="4"',
            "form~>lvsHcellBtn~>value=t",
            'form~>lvsHcellFile~>value="hcell.map"',
            "lvsRunModeCB(form)",
            "lvsHcellCB(form)",
            "form~>lvsIgnoreError~>value=t",
            "form~>lvsCaseBtn~>value=nil",
            "form~>lvsVirtualConn~>value=list(t nil)",
            'form~>lvsVirtualConnName~>value="VDD VSS"',
            "lvsVirtualConnCB(form)",
            "form~>customSvrfEnable~>value=t",
            'form~>customSvrfCommand~>value="LVS FILTER UNUSED OPTION"',
            "cadCustomSvrfCB(form)",
            'form~>cadBatchTasks=list(list("schLib" "schCell" "schematic" '
            '"layLib" "layCell" "layout"))',
        )
        mutate_state = (
            'form~>schLib~>value="changed"',
            'form~>layCell~>value="changed"',
            'form~>lvsRunsetFile~>value=""',
            'form~>cdlIncludeFile~>value=""',
            "form~>cdlIncludeBtn~>value=nil",
            'form~>lvsCpu~>value="1"',
            "form~>lvsHcellBtn~>value=nil",
            "form~>lvsIgnoreError~>value=nil",
            "form~>lvsCaseBtn~>value=t",
            "form~>lvsVirtualConn~>value=list(nil t)",
            "form~>customSvrfEnable~>value=nil",
            'form~>customSvrfCommand~>value=""',
            "form~>cadBatchTasks=nil",
        )
        restored = (
            'form~>schLib~>value=="profileLib"',
            'form~>schCell~>value=="profileCell"',
            'form~>layCell~>value=="profileCell"',
            f'form~>lvsRunsetFile~>value=="{tmp_path}/rules.lvs"',
            "form~>cdlIncludeBtn~>value",
            f'form~>cdlIncludeFile~>value=="{tmp_path}/header.cdl"',
            'form~>lvsCpu~>value=="4"',
            "form~>lvsHcellBtn~>value",
            f'form~>lvsHcellFile~>value=="{tmp_path}/hcell.map"',
            "form~>lvsIgnoreError~>value",
            "!form~>lvsCaseBtn~>value",
            "equal(form~>lvsVirtualConn~>value list(t nil))",
            "form~>customSvrfEnable~>value",
            'form~>customSvrfCommand~>value=="LVS FILTER UNUSED OPTION"',
            'equal(form~>cadBatchTasks list(list("schLib" "schCell" '
            '"schematic" "layLib" "layCell" "layout")))',
        )
    replay.write_text(
        "\n".join(
            (
                f'setShellEnvVar("CAD_HOME" "{install}")',
                f'load("{install}/tools/{entry}")',
                "procedure(SICO_profileMessage(severity flow message) "
                'printf("PROFILE_MESSAGE:%s:%s\\n" flow message) nil)',
                f"{display}()",
                f"form={form_name}",
                "controlsOk=and(form~>profileFile form~>profileLoad "
                "form~>profileSave form~>profileSaveAs)",
                'form~>outDirType~>value="Customize Directory"',
                "lvsOutDirCB(form)" if flow == "LVS" else "drcOutDirCB(form)",
                f'form~>outDirPath~>value="{tmp_path}/runs"',
                *set_state,
                f'form~>profileFile~>value="{profile}"',
                f'saveOk=SICO_profileSaveTo(form "{flow}" "{profile}")',
                *mutate_state,
                f'loadOk=SICO_profileLoadCB(form "{flow}")',
                "restoredOk=and(" + " ".join(restored) + ")",
                "currentRoot=pwd()",
                f'currentDirOk=and({flow.lower()}ProfileApply(form list(',
                '  list("run.root_type" "Current Directory")',
                '  list("run.root" "/stale/saved/root")))',
                '  form~>outDirType~>value=="Current Directory"',
                '  form~>outDirPath~>value==currentRoot)',
                f'projectDirOk=and({flow.lower()}ProfileApply(form list(',
                '  list("run.root_type" "Project Directory")',
                '  list("run.root" "/stale/saved/root")))',
                '  form~>outDirType~>value=="Project Directory"',
                f'  form~>outDirPath~>value=="{tmp_path}")',
                "when(and(controlsOk saveOk loadOk restoredOk currentDirOk ",
                "         projectDirOk)",
                f'  printf("{flow}_PROFILE_SAVE_LOAD_OK\\n"))',
                "when(form hiFormDone(form) hiDeleteForm(form))",
                "exit()",
                "",
            )
        ),
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment.update(
        {
            "DRC_DB_DIR": str(tmp_path),
            "LVS_DB_DIR": str(tmp_path),
            "RCE_DB_DIR": str(tmp_path),
        }
    )
    completed = subprocess.run(
        [virtuoso, "-nograph", "-nocdsinit", "-replay", str(replay), "-log", str(log)],
        text=True,
        capture_output=True,
        timeout=80,
        check=False,
        env=environment,
    )
    output = completed.stdout + completed.stderr
    if log.is_file():
        output += log.read_text(encoding="utf-8", errors="replace")
    assert completed.returncode == 0, output
    assert "*Error*" not in output
    assert "(reader)" not in output
    assert f"\\o {flow}_PROFILE_SAVE_LOAD_OK" in output
    assert profile.is_file()
    document = load_profile(profile, flow)
    assert document.flow == flow
