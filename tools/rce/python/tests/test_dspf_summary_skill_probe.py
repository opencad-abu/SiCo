from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from skill_test_support import rce_source_loads

CAD_ROOT = Path(__file__).resolve().parents[3]


def _skill_string(value: Path) -> str:
    """Return a SKILL-compatible double-quoted path literal."""
    return json.dumps(str(value))


@pytest.mark.skipif(
    os.environ.get("RCE_RUN_SKILL_PROBE") != "1",
    reason="set RCE_RUN_SKILL_PROBE=1 to run the Cadence dbAccess probe",
)
def test_summary_allows_dspf_analysis_for_ignored_lvs_mismatch(tmp_path: Path) -> None:
    dbaccess = shutil.which(os.environ.get("RCE_DBACCESS", "dbAccess"))
    if dbaccess is None:
        pytest.skip("dbAccess is unavailable")

    run_dir = tmp_path / "run"
    log_dir = run_dir / "log"
    log_dir.mkdir(parents=True)
    dspf = run_dir / "top.dspf"
    dspf.write_text("*|DSPF 1.0\n", encoding="utf-8")
    second_dspf = run_dir / "top_Cmin.dspf"
    second_dspf.write_text("*|DSPF 1.0\n", encoding="utf-8")
    marker = log_dir / "lvs-ignored-mismatch"
    marker.write_text("LVS mismatch explicitly ignored\n", encoding="utf-8")

    skill = "\n".join(
        (
            "mockAnalyzerSource=nil",
            'mockButtons=makeTable("rceSummaryProbeButtons" nil)',
            "mockStatusField=nil",
            "mockDetailsField=nil",
            "mockResultField=nil",
            "rceSummaryForm=nil",
            "defstruct(mockSummaryField labelText value items invisible)",
            "defstruct(mockSummaryForm rceSummaryStatus rceSummaryDetails "
            "rceSummaryResult rceSummaryResultRecords "
            "rceNetlistPath rceSummarySuccess rceSummaryMeta rceSummaryActionable "
            "rceSummaryAnalyzable rceSummaryIgnoredLvsMismatch displayed)",
            "procedure(hiCreateLabel(@key name labelText justification) "
            "mockStatusField=make_mockSummaryField(?labelText labelText))",
            "procedure(hiCreateHypertextField(@key name value hasVerticalScrollbar "
            "hasHorizontalScrollbar) mockDetailsField=make_mockSummaryField(?value value))",
            "procedure(hiCreateComboField(@key name prompt items value defValue editable "
            "callback invisible) mockResultField=make_mockSummaryField(?items items "
            "?value or(value defValue) ?invisible invisible))",
            "procedure(hiCreateVerticalBoxLayout(name @key items margins spacing) nil)",
            "procedure(hiCreateLayoutForm(name title layout @key callback mapCB help "
            "unmapAfterCB buttonLayout buttonDisabled formType dialogStyle baseName plist "
            "initialSize minSize maxSize sizePolicy) "
            "make_mockSummaryForm(?rceSummaryStatus mockStatusField "
            "?rceSummaryDetails mockDetailsField ?rceSummaryResult mockResultField))",
            "procedure(hiSetFormButtonEnabled(form button enabled) "
            "mockButtons[button]=enabled t)",
            # The production callbacks guard every access with hiIsForm().
            # Mark the probe structure as a valid form, matching Virtuoso HI.
            "procedure(hiIsForm(form) form!=nil)",
            "procedure(hiIsFormDisplayed(form) form~>displayed)",
            "procedure(hiDisplayForm(form) form~>displayed=t form)",
            "procedure(hiFormClose(form) form~>displayed=nil t)",
            "procedure(hiDeleteForm(form) t)",
            rce_source_loads("summary"),
            "procedure(rceDspfAnalyzerStart(source meta) "
            "mockAnalyzerSource=source t)",
            'meta=makeTable("rceSummaryProbeMeta" nil)',
            f"meta['runDir]={_skill_string(run_dir)}",
            f"meta['netlistPath]={_skill_string(dspf)}",
            "meta['outputType]=\"dspf\"",
            "meta['fileOutput]=t",
            "meta['launchLog]=\"\"",
            "rceSummaryDisplay(meta 0 t)",
            "if(and(rceSummaryForm~>rceSummaryIgnoredLvsMismatch "
            "rceSummaryForm~>rceSummaryAnalyzable "
            "rceSummaryForm~>rceSummaryActionable "
            "mockButtons[concat(\"Analyze DSPF\")]) "
            "then printf(\"RCE_SUMMARY_IGNORED_LVS_ANALYZABLE_OK\\n\"))",
            "started=rceSummaryAnalyzeCB(rceSummaryForm)",
            f"if(and(started mockAnalyzerSource=={_skill_string(dspf)}) "
            "then printf(\"RCE_SUMMARY_IGNORED_LVS_START_OK\\n\"))",
            f"deleteFile({_skill_string(dspf)})",
            "mockAnalyzerSource=nil",
            "rceSummaryAnalyzeCB(rceSummaryForm)",
            "if(and(!rceSummaryForm~>rceSummaryAnalyzable "
            "!mockButtons[concat(\"Analyze DSPF\")] !mockAnalyzerSource) "
            "then printf(\"RCE_SUMMARY_STALE_NETLIST_BLOCKED_OK\\n\"))",
            f"dspfFile=outfile({_skill_string(dspf)})",
            'fprintf(dspfFile "*|DSPF 1.0\\n")',
            "close(dspfFile)",
            "rceSummaryDisplay(meta 1 nil)",
            "rceSummaryAnalyzeCB(rceSummaryForm)",
            "if(and(rceSummaryForm~>rceSummaryIgnoredLvsMismatch "
            "!rceSummaryForm~>rceSummaryAnalyzable "
            "!mockButtons[concat(\"Analyze DSPF\")] !mockAnalyzerSource) "
            "then printf(\"RCE_SUMMARY_OTHER_FAILURE_BLOCKED_OK\\n\"))",
            'meta[\'outputType]="spef"',
            "rceSummaryDisplay(meta 0 t)",
            "if(and(rceSummaryForm~>rceSummaryIgnoredLvsMismatch "
            "!rceSummaryForm~>rceSummaryAnalyzable "
            "!mockButtons[concat(\"Analyze DSPF\")]) "
            "then printf(\"RCE_SUMMARY_NON_DSPF_BLOCKED_OK\\n\"))",
            f"meta['netlistPaths]=list({_skill_string(dspf)} "
            f"{_skill_string(second_dspf)})",
            "meta['corners]=list(\"RCmax\" \"Cmin\")",
            "meta['temperatures]=list(\"125\" \"-40\")",
            "meta['outputType]=\"dspf\"",
            "rceSummaryDisplay(meta 0 t)",
            "rceSummaryForm~>rceSummaryResult~>value=\"Cmin\"",
            "selected=rceSummaryResultCB(rceSummaryForm)",
            "mockAnalyzerSource=nil",
            "rceSummaryAnalyzeCB(rceSummaryForm)",
            f"if(and(selected=={_skill_string(second_dspf)} "
            f"mockAnalyzerSource=={_skill_string(second_dspf)} "
            "!rceSummaryForm~>rceSummaryResult~>invisible) "
            "then printf(\"RCE_SUMMARY_MULTI_RESULT_OK\\n\"))",
            "meta['outputType]=\"view\" meta['fileOutput]=nil",
            "meta['netlistPaths]=nil meta['netlistPath]=nil",
            "meta['corners]=nil",
            "rceSummaryDisplay(meta 0 t)",
            "unless(and(rceSummaryForm~>rceSummaryIgnoredLvsMismatch",
            "  rexMatchp(\"ignored LVS mismatch\" mockStatusField~>labelText)",
            "  !rexMatchp(\"completed successfully\" mockStatusField~>labelText))",
            '  error("Native view summary lost ignored LVS warning"))',
            'printf("RCE_SUMMARY_NATIVE_WARNING_OK\\n")',
            "exit()",
        )
    )
    completed = subprocess.run(
        [dbaccess],
        input=skill,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "RCE_SUMMARY_IGNORED_LVS_ANALYZABLE_OK" in output
    assert "RCE_SUMMARY_IGNORED_LVS_START_OK" in output
    assert "RCE_SUMMARY_STALE_NETLIST_BLOCKED_OK" in output
    assert "RCE_SUMMARY_OTHER_FAILURE_BLOCKED_OK" in output
    assert "RCE_SUMMARY_NON_DSPF_BLOCKED_OK" in output
    assert "RCE_SUMMARY_MULTI_RESULT_OK" in output
    assert "RCE_SUMMARY_NATIVE_WARNING_OK" in output
