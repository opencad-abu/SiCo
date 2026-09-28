from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest


CAD_ROOT = Path(__file__).resolve().parents[3]
RUN_PROBE = os.environ.get("RCE_RUN_SKILL_PROBE") == "1"


def _assert_clean(output: str) -> None:
    assert "*Error*" not in output
    assert "(reader)" not in output
    assert "still unclosed on EOF" not in output


def _run_dbaccess(skill: str, *, env: dict[str, str] | None = None) -> str:
    dbaccess = shutil.which(os.environ.get("RCE_DBACCESS", "dbAccess"))
    if dbaccess is None:
        pytest.skip("dbAccess is unavailable")
    completed = subprocess.run(
        [dbaccess],
        input=skill,
        text=True,
        capture_output=True,
        timeout=40,
        check=False,
        env=env,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    _assert_clean(output)
    return output


def _probe_env(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    for name in ("DRC_DB_DIR", "LVS_DB_DIR", "RCE_DB_DIR"):
        env[name] = str(tmp_path)
    return env


def test_batch_source_contracts() -> None:
    gui = (CAD_ROOT / "common/skill/SICO_batchGui.il").read_text(encoding="utf-8")
    rows = (CAD_ROOT / "common/skill/SICO_batchRows.il").read_text(encoding="utf-8")
    monitor = (CAD_ROOT / "common/skill/SICO_batchMonitor.il").read_text(
        encoding="utf-8"
    )
    batch_gui = (CAD_ROOT / "common/skill++/BATCHGUI.ils").read_text(
        encoding="utf-8"
    )
    rce = (CAD_ROOT / "rce/skill++/RCEBATCH.ils").read_text(encoding="utf-8")
    rce_loader = (CAD_ROOT / "rce/skill++/RCE.ils").read_text(encoding="utf-8")

    assert '?callback    list("cadBatchScopeCB(hiGetCurrentForm())")' in batch_gui
    assert "?doubleClickCB 'cadBatchTaskDoubleClickCB" in gui
    assert "~>hiContextMenu=cadBatchCreateTaskContextMenu()" in gui
    assert "?name 'batchTaskRemove" not in gui
    assert "?name 'batchTaskClear" not in gui
    assert "procedure(cadBatchRemoveCB(form)" in rows
    assert "procedure(cadBatchClearCB(form)" in rows
    assert "editIndex=form~>cadBatchEditIndex" in rows
    add_update = rows.split("procedure(cadBatchAddUpdateCB(form)", 1)[1].split(
        "procedure(cadBatchRemoveCB(form)", 1
    )[0]
    assert "cadBatchSelectedIndex(form)" not in add_update
    assert 'fields=parseString(line "\\t\\r\\n" t)' in monitor
    assert 'form~>inpType~>value="OA"' in gui
    assert "form~>inpType~>enabled=!multiple" in gui
    assert "procedure(rceBatchPublicationRequiredP(form viewRequest)" in rce
    assert "if(viewRequest then t else nil)" in rce
    assert "rceCompleteViewRequest(request spec['launchLog])" in rce
    assert "isCallable('rceBatchAdapterRevision)" in rce_loader
    assert (
        'rceBatchAdapterRevision()=="20260924.flow.environment.v3"'
        in rce_loader
    )
    assert rce_loader.index('"/skill++/RCECB.ils"') < rce_loader.index(
        '"/skill++/RCEBATCH.ils"'
    ) < rce_loader.index('"/skill++/RCEGUI.ils"')
    for relative in ("lvs/skill++/LVSRUN.ils", "rce/skill++/RCEBATCH.ils"):
        text = (CAD_ROOT / relative).read_text(encoding="utf-8")
        assert 'form~>inpType~>value=="OA"' in text


@pytest.mark.skipif(not RUN_PROBE, reason="set RCE_RUN_SKILL_PROBE=1")
def test_batch_monitor_distinguishes_lvs_warning_from_passed() -> None:
    output = _run_dbaccess("\n".join((
        f'load("{CAD_ROOT}/common/skill/SICO_batchMonitor.il")',
        'rows=list(list("1" "001" "top" "succeeded_with_warnings" "0" "/run" "/config" "/log" "-" "-" "1" "/output"))',
        'unless(nth(2 car(cadBatchMonitorChoices(rows)))=="LVS Warning" error("Missing warning label"))',
        'summary=cadBatchMonitorSummary(rows)',
        'unless(and(rexMatchp("Passed 0" summary) rexMatchp("LVS Warnings 1" summary)) error("Warning counted as pass"))',
        'printf("CAD_BATCH_WARNING_MONITOR_OK\\n")',
        'exit()',
    )))
    assert "CAD_BATCH_WARNING_MONITOR_OK" in output


def test_lvs_batch_open_result_propagates_the_prompt_outcome() -> None:
    runner = (CAD_ROOT / "lvs/skill++/LVSRUN.ils").read_text(encoding="utf-8")
    loader = (CAD_ROOT / "lvs/skill++/LVS.ils").read_text(encoding="utf-8")
    prompt = runner.split("procedure(lvsPromptOpenRve(config svdb)", 1)[1].split(
        "\nprocedure(", 1
    )[0]

    assert 'return(lvsOpenRve(config svdb))' in prompt
    assert "return(t)" in prompt
    assert 'lvsRveLauncherRevision()=="20260828.prompt.return.v1"' in loader


@pytest.mark.skipif(not RUN_PROBE, reason="set RCE_RUN_SKILL_PROBE=1")
def test_lvs_batch_open_result_monitor_with_dbaccess(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    config = run_dir / "log/lvs.toml"
    svdb = run_dir / "db/svdb.top"
    config.parent.mkdir(parents=True)
    config.write_text("", encoding="utf-8")
    svdb.mkdir(parents=True)
    skill = "\n".join(
        (
            f'setShellEnvVar("SICO_HOME" "{CAD_ROOT.parent}")',
            f'load("{CAD_ROOT}/common/skill/SICO_toml.il")',
            f'load("{CAD_ROOT}/common/skill/SICO_batchAdapter.il")',
            f'load("{CAD_ROOT}/lvs/skill++/LVSRUN.ils")',
            f'load("{CAD_ROOT}/common/skill/SICO_batchMonitor.il")',
            "defstruct(probeLvsResultForm cadBatchMeta)",
            'probeAnswer="YES" probeLaunchCalls=0 probeWarnings=0',
            "procedure(GUI_askApp(flow text) probeAnswer)",
            "procedure(lvsOpenRve(config svdb) "
            "probeLaunchCalls=probeLaunchCalls+1 'probeCid)",
            'probeSpec=makeTable("probeLvsSpec" nil)',
            f'probeSpec[\'config]="{config}" probeSpec[\'resultPath]="{svdb}"',
            'meta=makeTable("probeLvsMeta" nil) meta[\'flow]="LVS"',
            "form=make_probeLvsResultForm(?cadBatchMeta meta)",
            "procedure(cadBatchMonitorSelectedSpec(form) probeSpec)",
            "procedure(cadBatchMessage(severity flow message) "
            "probeWarnings=probeWarnings+1 nil)",
            "cadBatchMonitorOpenResultCB(form)",
            'probeAnswer="NO"',
            "cadBatchMonitorOpenResultCB(form)",
            "if(and(probeLaunchCalls==1 probeWarnings==0) "
            'then printf("LVS_BATCH_OPEN_RESULT_OK\\n"))',
            "exit()",
        )
    )
    output = _run_dbaccess(skill, env=_probe_env(tmp_path))
    assert "LVS_BATCH_OPEN_RESULT_OK" in output


@pytest.mark.skipif(not RUN_PROBE, reason="set RCE_RUN_SKILL_PROBE=1")
@pytest.mark.parametrize(
    ("flow", "entry", "revision", "start", "display"),
    (
        ("DRC", "drc/skill++/DRC.ils", "drcLoaderRevision", "drcBatchStart", "cadDisplayDrcForm"),
        ("LVS", "lvs/skill++/LVS.ils", "lvsLoaderRevision", "lvsBatchStart", "cadDisplayLvsForm"),
        ("RCE", "rce/skill++/RCE.ils", "rceLoaderRevision", "rceBatchStart", "cadDisplayRceForm"),
    ),
)
def test_batch_frontend_loaders_are_reader_clean(
    tmp_path: Path, flow: str, entry: str, revision: str, start: str, display: str
) -> None:
    skill = "\n".join(
        (
            f'setShellEnvVar("SICO_HOME" "{CAD_ROOT.parent}")',
            f'load("{CAD_ROOT}/{entry}")',
            f"if(and(isCallable('{revision}) isCallable('{start}) ",
            f"isCallable('{display}) isCallable('cadBatchCoreRevision)) ",
            f'then printf("{flow}_BATCH_LOADER_OK\\n"))',
            "exit()",
        )
    )
    output = _run_dbaccess(skill, env=_probe_env(tmp_path))
    assert f"{flow}_BATCH_LOADER_OK" in output


@pytest.mark.skipif(not RUN_PROBE, reason="set RCE_RUN_SKILL_PROBE=1")
def test_batch_rows_tsv_scope_and_monitor_with_dbaccess(tmp_path: Path) -> None:
    status = tmp_path / "status.tsv"
    status.write_text(
        "index\tid\tlabel\tstatus\texit_code\trun_dir\tconfig\tlaunch_log\t"
        "started_at\tfinished_at\tduration_seconds\tresult_path\t"
        "publication_status\tpublication_message\n"
        "1\t001\tlib/cell/layout\trunning\t\t/run\t/config\t/log\t\t\t"
        "1.2\t/result\t-\tmessage\n",
        encoding="utf-8",
    )
    sources = tuple(
        CAD_ROOT / relative
        for relative in (
            "common/skill/SICO_toml.il",
            "common/skill/SICO_batchAdapter.il",
            "common/skill/SICO_batchGui.il",
            "common/skill/SICO_batchRows.il",
            "common/skill/SICO_batchCore.il",
            "common/skill/SICO_batchMonitor.il",
        )
    )
    skill = "\n".join(
        (
            "defstruct(probeField value invisible enabled choices selected labelText)",
            "defstruct(probeForm cadBatchFlow cadBatchTasks cadBatchEditIndex "
            "cadBatchScopeActive "
            "cadBatchSingleInputType batchRunScope batchTaskLay batchParallelCells "
            "layInp designInp inpType batchTaskReport batchLayLib batchLayCell "
            "batchLayView cadBatchMeta cadBatchMonitorRows cadBatchMonitorReport "
            "cadBatchMonitorStatus)",
            *(f'load("{source}")' for source in sources),
            "probeSelected=nil probeSelectNotified=nil probeLvsInpCalls=0",
            "procedure(hiGetCurrentForm() probeCurrentForm)",
            "procedure(hiReportGetSelectedItems(field) probeSelected)",
            "procedure(hiReportDeselectAllItems(field notify) probeSelected=nil t)",
            "procedure(hiReportSelectItem(field index notify) "
            "probeSelected=list(index) probeSelectNotified=notify t)",
            "procedure(probeDrcEnterMultiple(form) form~>layInp~>invisible=t t)",
            "procedure(probeDrcLeaveMultiple(form) form~>layInp~>invisible=nil t)",
            "procedure(probeLvsEnterMultiple(form) form~>designInp~>invisible=t t)",
            "procedure(probeLvsLeaveMultiple(form) "
            "probeLvsInpCalls=probeLvsInpCalls+1 "
            "form~>designInp~>invisible=nil t)",
            "cadBatchRegisterFlowAdapter(\"DRC\" nil 'probeDrcEnterMultiple "
            "'probeDrcLeaveMultiple nil nil)",
            "cadBatchRegisterFlowAdapter(\"LVS\" t 'probeLvsEnterMultiple "
            "'probeLvsLeaveMultiple nil nil)",
            "procedure(cadBatchRowValidP(form row) t)",
            "row1=list(\"libA\" \"cellA\" \"layout\")",
            "row2=list(\"libB\" \"cellB\" \"layout\")",
            "rowForm=make_probeForm(?cadBatchFlow \"DRC\" "
            "?cadBatchTasks list(row1 row2) "
            "?batchTaskReport make_probeField(?choices nil) "
            "?batchLayLib make_probeField(?value \"\") "
            "?batchLayCell make_probeField(?value \"\") "
            "?batchLayView make_probeField(?value \"\"))",
            "probeCurrentForm=rowForm",
            "cadBatchTaskReportCB('batchTaskReport list(0))",
            "singleClickOk=and(rowForm~>batchLayLib~>value==\"\" "
            "rowForm~>batchLayCell~>value==\"\" "
            "rowForm~>cadBatchEditIndex==nil)",
            "cadBatchTaskDoubleClickCB(\"batchTaskReport\" 0)",
            "doubleClickOk=and(rowForm~>batchLayLib~>value==\"libA\" "
            "rowForm~>batchLayCell~>value==\"cellA\" "
            "rowForm~>cadBatchEditIndex==0)",
            "rowForm~>batchLayLib~>value=\"libX\" "
            "rowForm~>batchLayCell~>value=\"cellX\" "
            "cadBatchAddUpdateCB(rowForm)",
            "updateOk=and(length(rowForm~>cadBatchTasks)==2 "
            "nth(0 car(rowForm~>cadBatchTasks))==\"libX\" "
            "rowForm~>cadBatchEditIndex==nil probeSelected==list(0))",
            "rowForm~>batchLayLib~>value=\"libC\" "
            "rowForm~>batchLayCell~>value=\"cellC\" "
            "cadBatchAddUpdateCB(rowForm)",
            "appendOk=and(length(rowForm~>cadBatchTasks)==3 "
            "nth(0 nth(2 rowForm~>cadBatchTasks))==\"libC\" "
            "probeSelected==list(2))",
            "probeSelected=list(1) "
            "cadBatchTaskContextMenuCB(nil rowForm 'batchTaskReport) "
            "cadBatchDeleteSelectedMappingCB()",
            "deleteOk=and(length(rowForm~>cadBatchTasks)==2 "
            "nth(0 nth(1 rowForm~>cadBatchTasks))==\"libC\" "
            "rowForm~>cadBatchEditIndex==nil cadBatchTaskContextForm==nil)",
            "rowForm~>cadBatchEditIndex=0 cadBatchClearCB(rowForm)",
            "clearOk=and(!rowForm~>cadBatchTasks "
            "rowForm~>cadBatchEditIndex==nil !probeSelected)",
            "rowOps=and("
            "equal(cadBatchReplaceAt(list(1 2 3) 1 9) list(1 9 3)) "
            "equal(cadBatchRemoveAt(list(1 2 3) 0) list(2 3)))",
            "drcScope=make_probeForm(?cadBatchFlow \"DRC\" "
            "?batchRunScope make_probeField(?value \"Multiple Cells\") "
            "?batchTaskLay make_probeField(?invisible t) "
            "?batchParallelCells make_probeField(?invisible t) "
            "?layInp make_probeField(?invisible nil))",
            "cadBatchScopeCB(drcScope)",
            "drcMultiple=and(!drcScope~>batchTaskLay~>invisible "
            "!drcScope~>batchParallelCells~>invisible drcScope~>layInp~>invisible)",
            "drcScope~>batchRunScope~>value=\"Single Cell\" cadBatchScopeCB(drcScope)",
            "drcSingle=and(drcScope~>batchTaskLay~>invisible "
            "drcScope~>batchParallelCells~>invisible !drcScope~>layInp~>invisible)",
            "lvsScope=make_probeForm(?cadBatchFlow \"LVS\" "
            "?batchRunScope make_probeField(?value \"Multiple Cells\") "
            "?batchTaskLay make_probeField(?invisible t) "
            "?batchParallelCells make_probeField(?invisible t) "
            "?designInp make_probeField(?invisible nil) "
            "?inpType make_probeField(?value \"CDL+GDS\" ?enabled t))",
            "cadBatchScopeCB(lvsScope)",
            "lvsMultiple=and(lvsScope~>inpType~>value==\"OA\" "
            "!lvsScope~>inpType~>enabled lvsScope~>designInp~>invisible)",
            "lvsScope~>batchRunScope~>value=\"Single Cell\" cadBatchScopeCB(lvsScope)",
            "lvsSingle=and(lvsScope~>inpType~>value==\"CDL+GDS\" "
            "lvsScope~>inpType~>enabled !lvsScope~>designInp~>invisible "
            "probeLvsInpCalls==1)",
            f'parsedRows=cadBatchMonitorReadRows("{status}")',
            "parsed=and(length(parsedRows)==1 length(car(parsedRows))>=14 "
            "nth(4 car(parsedRows))==\"\" nth(8 car(parsedRows))==\"\" "
            "nth(9 car(parsedRows))==\"\")",
            "procedure(hiCreateReportField(@key name title headers choices "
            "selectMode altRowHilight) probeReport=make_probeField(?choices choices))",
            "procedure(SICO_logo() \"ACME\")",
            "procedure(hiCreateLabel(@key name labelText justification) "
            "probeStatus=make_probeField(?labelText labelText))",
            "procedure(hiCreateVerticalBoxLayout(name @key items margins spacing) items)",
            "procedure(hiCreateLayoutForm(name title layout @key buttonLayout "
            "dialogStyle initialSize minSize sizePolicy) probeTitle=title "
            "make_probeForm(?cadBatchMonitorReport probeReport "
            "?cadBatchMonitorStatus probeStatus))",
            "procedure(hiSetFormButtonEnabled(form button enabled) t)",
            "procedure(hiDisplayForm(form) t)",
            "procedure(hiRegTimer(command ticks) t)",
            "spec1=cadBatchMakeSpec(\"001\" \"lib/a/layout\" \"/a.toml\" "
            "\"/a\" \"true\" \"\" \"/a.log\" \"/a/result\" \"/a\" nil)",
            "spec2=cadBatchMakeSpec(\"002\" \"lib/b/layout\" \"/b.toml\" "
            "\"/b\" \"true\" \"\" \"/b.log\" \"/b/result\" \"/b\" nil)",
            "meta=makeTable(\"probeMeta\" nil) meta['id]=\"probe\" "
            "meta['flow]=\"DRC\" meta['specs]=list(spec1 spec2) "
            "meta['tempDir]=\"/launch/.cad\" "
            "meta['statusTsv]=\"/no/such/status.tsv\" meta['done]=nil "
            "meta['cancelRequested]=nil meta['batchLog]=\"/batch.log\"",
            "monitorForm=cadBatchMonitorDisplay(meta)",
            "monitorOk=and(monitorForm length(probeReport~>choices)==2 "
            "probeStatus~>labelText==\"Total 2   Pending 2   Running 0   "
            "Publishing 0   Passed 0   Failed 0   Canceled 0\" "
            "probeTitle==\"ACME::DRC Multi-Cell Summary\")",
            "if(and(singleClickOk doubleClickOk updateOk appendOk deleteOk clearOk "
            "rowOps drcMultiple drcSingle lvsMultiple "
            "lvsSingle parsed monitorOk) then printf(\"CAD_BATCH_SHARED_PROBE_OK\\n\"))",
            "exit()",
        )
    )
    output = _run_dbaccess(skill)
    assert "CAD_BATCH_SHARED_PROBE_OK" in output


@pytest.mark.skipif(not RUN_PROBE, reason="set RCE_RUN_SKILL_PROBE=1")
def test_rce_batch_publication_protocol_with_dbaccess(tmp_path: Path) -> None:
    status = tmp_path / "status.tsv"
    publication = tmp_path / "publication.tsv"
    status.write_text(
        "index\tid\tlabel\tstatus\texit_code\trun_dir\tconfig\tlaunch_log\t"
        "started_at\tfinished_at\tduration_seconds\tresult_path\t"
        "publication_status\tpublication_message\n"
        "1\t001\tlib/cell/layout\tawaiting_publication\t0\t/run\t/config\t"
        "/log\t-\t-\t1\tlib/cell/qrc\tpending\t-\n",
        encoding="utf-8",
    )
    skill = "\n".join(
        (
            f'setShellEnvVar("SICO_HOME" "{CAD_ROOT.parent}")',
            f'load("{CAD_ROOT}/rce/skill++/RCE.ils")',
            "probeCompleted=0 probeSources=nil probeViews=nil probeFinalize=nil",
            "procedure(rceCreateNetlistViewFromRequest(request launchLog) "
            "probeCompleted=probeCompleted+1 "
            "probeSources=append1(probeSources caddr(request)) "
            "probeViews=append1(probeViews nth(5 request)) t)",
            "procedure(cadBatchControllerCommand(flow command manifest @optional argument) "
            "probeFinalize=list(flow command manifest argument) \"/bin/true\")",
            "request=list(\"group\" "
            "list(\"text\" \"lib\" \"cell\" \"/out\" \"dspf\" \"/cds.lib\") "
            "list(\"text\" \"lib\" \"cell\" \"/out.reduced\" \"dspf\" "
            "\"/cds.lib\" \"dspfText_reduced\"))",
            "spec=cadBatchMakeSpec(\"001\" \"lib/cell/layout\" \"/config\" "
            "\"/run\" \"true\" \"\" \"/log\" \"lib/cell/qrc\" "
            "\"lib/cell/qrc\" t)",
            "cadBatchSpecSet(spec 'viewRequest request)",
            "meta=makeTable(\"publicationMeta\" nil) meta['specs]=list(spec) "
            f'meta[\'statusTsv]=\"{status}\" meta[\'publicationTsv]=\"{publication}\" ',
            f'meta[\'manifest]=\"{tmp_path}/batch.toml\" ',
            "meta['cancelRequested]=nil",
            "result=rceBatchPostProcess(meta 130)",
            f'port=infile("{publication}") gets(header port) gets(line port) close(port)',
            "fields=parseString(line \"\\t\\r\\n\" t)",
            "required=and(rceBatchPublicationRequiredP(nil request) "
            "rceBatchPublicationRequiredP(nil list(\"text\" \"lib\" \"cell\" "
            "\"/out\" \"dspf\")))",
            "if(and(result==0 probeCompleted==2 required "
            "equal(probeSources list(\"/out\" \"/out.reduced\")) "
            "equal(probeViews list(nil \"dspfText_reduced\")) "
            "probeFinalize==list(\"RCE\" \"finalize\" "
            f'\"{tmp_path}/batch.toml\" \"{publication}\") '
            "nth(0 fields)==\"001\" nth(1 fields)==\"succeeded\") "
            "then printf(\"RCE_BATCH_PUBLICATION_OK\\n\"))",
            "exit()",
        )
    )
    output = _run_dbaccess(skill, env=_probe_env(tmp_path))
    assert "RCE_BATCH_PUBLICATION_OK" in output
