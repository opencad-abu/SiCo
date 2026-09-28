from __future__ import annotations

import os
from pathlib import Path

import pytest

from rcepy.config import load_config
from test_absolute_path_skill_probe import _install_tree, _run_probe, _write
from test_term_order_skill_probe import _run


@pytest.mark.skipif(
    os.environ.get("RCE_RUN_SKILL_PROBE") != "1", reason="set RCE_RUN_SKILL_PROBE=1"
)
def test_busy_run_is_rejected_before_config_and_cdf_and_refresh_keeps_corners(
    tmp_path: Path,
):
    install = _install_tree(tmp_path)
    launch = tmp_path / "launch"
    _write(launch / "cds.lib", "")
    for tech in ("tech_a", "tech_b"):
        for corner in ("Cmax", "RCmax"):
            _write(launch / tech / corner / "qrcTechFile")
    run_root = launch / "runs"
    run_root.mkdir()
    _run_probe(
        tmp_path,
        flow="RCE",
        entry=f"{install}/tools/rce/skill++/RCE.ils",
        commands=(
            'ddCreateLib("rce_review" strcat(pwd() "/rce_review"))',
            'foreach(view list("schematic" "layout")',
            '  cv=dbOpenCellViewByType("rce_review" "top" view',
            '    if(view=="layout" then "maskLayout" else "schematic") "w")',
            "  dbSave(cv) dbClose(cv))",
            "cadDisplayRceForm() form=rceForm",
            'form~>inpType~>value="OA" rceInpCB(form)',
            'form~>schLib~>value="rce_review" form~>schCell~>value="top"',
            'form~>schView~>value="schematic"',
            'form~>layLib~>value="rce_review" form~>layCell~>value="top"',
            'form~>layView~>value="layout"',
            'form~>outDirPath~>value=strcat(pwd() "/runs")',
            'form~>extTool~>value="QRC"',
            'form~>rcTechDirPath~>value=strcat(pwd() "/tech_a") rceRcTechDirPathCB(form)',
            'form~>outType~>value="dspf" rceOutTypeCB(form)',
            "form~>createNetlistView~>value=nil form~>pinOrderBtn~>value=nil",
            'form~>temp~>value="25"',
            'unless(rcePrint(form) error("Initial config failed"))',
            "reservation=rceReserveRun(form)",
            'unless(reservation error("Reservation failed"))',
            "putd('GUI_messageApp lambda((severity title message) t))",
            "originalCheck=getd('rceCheckOaTermOrder)",
            'putd(\'rceCheckOaTermOrder lambda((form) error("Busy run reached CDF write")))',
            'form~>temp~>value="105"',
            'when(rceStart(form) error("Busy run started"))',
            'when(rcePrint(form) error("Busy run rewrote config"))',
            "form~>cadBatchTasks=list(cadBatchDesignValues(form))",
            'form~>batchRunScope~>value="Multiple Cells"',
            'when(rceBatchStart(form) error("Busy batch started"))',
            "rceReleaseRun(reservation)",
            "putd('rceCheckOaTermOrder originalCheck)",
            'form~>cornerType~>value="Multiple Corners" rceCornerTypeCB(form)',
            "foreach(field form~>rceCornerSelectFields field~>value=list(nil))",
            "nth(0 form~>rceCornerSelectFields)~>value=list(t)",
            'nth(0 form~>rceCornerTempFields)~>value="61"',
            'nth(1 form~>rceCornerTempFields)~>value="97"',
            'form~>rcTechDirPath~>value=strcat(pwd() "/tech_b") rceRcTechDirPathCB(form)',
            'unless(equal(rceSelectedProcessCorners(form) list("Cmax")) error("Refresh changed selected corners"))',
            'unless(equal(rceSelectedCornerTemperatures(form) list("61")) error("Refresh changed temperatures"))',
            'unless(nth(1 form~>rceCornerTempFields)~>value=="97" error("Refresh changed unselected temperature"))',
            "hiFormClose(form) hiDeleteForm(form)",
        ),
        env_updates={
            "CAD_HOME": str(install),
            "CDS_LIB": "cds.lib",
            "RCE_DB_DIR": str(run_root),
            "QUANTUS_TECH_DIR": f"probe,{launch}/tech_a",
            "RCE_DEF_TOOL": "QRC",
            "RCE_OUTPUT_CHOICES": "dspf,sp,view,spef",
            "RCE_CORNER": "RCmax",
        },
    )
    assert (
        load_config(run_root / "rce_review.top/rce.toml").text("extract", "temperature")
        == "25"
    )
    assert not list((run_root / ".cad-rce-locks").glob("*.json"))


@pytest.mark.skipif(
    os.environ.get("RCE_RUN_SKILL_PROBE") != "1", reason="set RCE_RUN_SKILL_PROBE=1"
)
def test_run_reservations_release_on_launch_failure_cancel_and_publication_error(
    tmp_path,
):
    install = _install_tree(tmp_path)
    launch = tmp_path / "launch"
    _write(launch / "cds.lib", "")
    _write(launch / "tech/Typ/qrcTechFile")
    (launch / "runs").mkdir()
    commands = (
        f'load("{install}/tools/rce/skill++/RCE.ils")',
        'procedure(auditAssert(value message) unless(value error("RCE_TEST: %s" message)))',
        'ddCreateLib("audit" strcat(pwd() "/audit"))',
        'foreach(cell list("top" "other") foreach(view list("schematic" "layout")',
        '  cv=dbOpenCellViewByType("audit" cell view',
        '    if(view=="layout" then "maskLayout" else "schematic") "w")',
        "  dbSave(cv) dbClose(cv)))",
        "cadDisplayRceForm() form=rceForm",
        'form~>inpType~>value="OA" rceInpCB(form)',
        'form~>layLib~>value="" form~>layCell~>value=""',
        'auditAssert(!rceRunDirectory(form) "Empty fields produced a lock directory")',
        'row=list("audit" "top" "schematic" "audit" "top" "layout")',
        'other=list("audit" "other" "schematic" "audit" "other" "layout")',
        "cadBatchSetDesignValues(form row)",
        'form~>outDirPath~>value=strcat(pwd() "/runs")',
        'form~>extTool~>value="QRC"',
        'form~>rcTechDirPath~>value=strcat(pwd() "/tech") rceRcTechDirPathCB(form)',
        'form~>outType~>value="dspf" rceOutTypeCB(form)',
        "form~>createNetlistView~>value=nil form~>pinOrderBtn~>value=nil",
        "putd('GUI_messageApp lambda((severity title message) t))",
        "putd('GUI_flowLogOpen lambda((flow path) nil))",
        "putd('rceSummaryDisplay lambda((meta status viewOk) nil))",
        "savedBackup=getd('rceBackupRunData)",
        "putd('rceBackupRunData lambda((run @optional reservation) 1))",
        'auditAssert(!rceStart(form) "Failed launch preparation was accepted")',
        "putd('rceBackupRunData savedBackup)",
        'held=rceReserveRun(form) auditAssert(held "Launch failure leaked reservation")',
        "rceReleaseRun(held)",
        "savedWrap=getd('SICO_lsfWrapCommand)",
        "putd('SICO_lsfWrapCommand lambda((@rest args) nil))",
        'auditAssert(!errset(rceStart(form) nil) "Invalid IPC command was accepted")',
        "putd('SICO_lsfWrapCommand savedWrap)",
        'held=rceReserveRun(form) auditAssert(held "IPC exception leaked reservation")',
        "rceReleaseRun(held)",
        "form~>cadBatchTasks=list(row other)",
        'form~>batchRunScope~>value="Multiple Cells"',
        "cadBatchSetDesignValues(form other) held=rceReserveRun(form)",
        "cadBatchSetDesignValues(form row)",
        'auditAssert(!rceBatchStart(form) "Partially busy batch was accepted")',
        'auditAssert(equal(cadBatchDesignValues(form) row) "Batch did not restore form")',
        'first=rceReserveRun(form) auditAssert(first "Partial reservation leaked")',
        "rceReleaseRun(first) rceReleaseRun(held)",
        "putd('cadBatchLaunch lambda((meta) auditMeta=meta nil))",
        'auditAssert(!rceBatchStart(form) "Failed batch launch was accepted")',
        "foreach(res auditMeta['runReservations]",
        '  auditAssert(rceRunLockCommand("reserve" car(res) cadr(res))==0 "Batch failure leaked reservation")',
        "  rceReleaseRun(res))",
        "putd('cadBatchLaunch lambda((meta) auditMeta=meta t))",
        'auditAssert(rceBatchStart(form) "Batch setup failed")',
        "foreach(res auditMeta['runReservations]",
        '  auditAssert(rceRunLockCommand("check" car(res) cadr(res))==0 "Queued batch lost reservation"))',
        'auditAssert(rceBatchPostProcess(auditMeta 130)==130 "Cancel status changed")',
        "foreach(res auditMeta['runReservations]",
        '  auditAssert(rceRunLockCommand("reserve" car(res) cadr(res))==0 "Cancel leaked reservation")',
        "  rceReleaseRun(res))",
        'auditAssert(rceBatchStart(form) "Second batch setup failed")',
        'putd(\'rceBatchPublishResults lambda((meta status) error("Injected batch publication failure")))',
        'auditAssert(!errset(rceBatchPostProcess(auditMeta 0) nil) "Publication failure was not exercised")',
        "foreach(res auditMeta['runReservations]",
        '  auditAssert(rceRunLockCommand("reserve" car(res) cadr(res))==0 "Publication failure leaked reservation")',
        "  rceReleaseRun(res))",
        "held=rceReserveRun(form)",
        'putd(\'rceCompleteViewRequest lambda((request log) error("Injected single publication failure")))',
        "putd('rceSummaryDisplay lambda((meta status viewOk)",
        '  auditAssert(!viewOk "Failed publication reached successful summary")',
        '  auditAssert(rceRunLockCommand("check" car(held) cadr(held))==0 "Lock released before summary")',
        "  auditSummary=t nil))",
        'rceIpcSetMeta(867 list(nil "" nil list("probe") makeTable("auditSummaryMeta" nil) nil held))',
        "rceIpcPostFunc(867 0)",
        'auditAssert(auditSummary "Summary was not reached after publication failure")',
        'next=rceReserveRun(form) auditAssert(next "Single publication failure leaked reservation")',
        "rceReleaseRun(next)",
        "auditSummary=nil",
        "putd('rceSummaryDisplay lambda((meta status viewOk) auditSummary=list(status viewOk) nil))",
        'putd(\'SICO_lsfWrapCommand lambda((command @rest args) strcat(command " --dry-run")))',
        "savedSetMeta=getd('rceIpcSetMeta)",
        "putd('rceIpcSetMeta lambda((cid meta)",
        "  when(meta auditCid=cid auditReservation=nth(6 meta))",
        "  apply(savedSetMeta list(cid meta))))",
        'auditAssert(rceStart(form) "Real IPC launch failed")',
        'auditAssert(rceRunLockCommand("reserve" car(auditReservation) "other")==1 "Active IPC lost reservation")',
        "ipcWait(auditCid 1 20)",
        'auditAssert(equal(auditSummary list(0 t)) "Real IPC completion callback failed")',
        'next=rceReserveRun(form) auditAssert(next "Real IPC completion leaked reservation")',
        "rceReleaseRun(next)",
        "hiFormClose(form) hiDeleteForm(form)",
        'printf("RCE_AUDIT_LIFECYCLE_OK\\n")',
    )
    output = _run(
        launch,
        {
            "CAD_HOME": str(install),
            "CDS_LIB": "cds.lib",
            "RCE_DB_DIR": str(launch / "runs"),
            "QUANTUS_TECH_DIR": f"probe,{launch}/tech",
            "RCE_DEF_TOOL": "QRC",
            "RCE_OUTPUT_CHOICES": "dspf,sp,view,spef",
            "RCE_CORNER": "Typ",
        },
        "lifecycle",
        "\n".join(commands),
    )
    assert "\\o RCE_AUDIT_LIFECYCLE_OK" in output, output
    unexpected_errors = [
        line
        for line in output.splitlines()
        if "*Error*" in line and "Injected single publication failure" not in line
    ]
    assert not unexpected_errors, "\n".join(unexpected_errors)
    assert not list((launch / "runs/.cad-rce-locks").glob("*.json"))
