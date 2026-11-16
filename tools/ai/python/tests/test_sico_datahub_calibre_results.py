"""Conservative Calibre DRC/LVS result parsing."""

from pathlib import Path

import pytest
from sico_datahub.calibre.drc_results import parse_drc_results, parse_drc_summary
from sico_datahub.calibre.lvs_results import parse_lvs_report
from sico_datahub.errors import HubError


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_drc_clean_requires_explicit_summary_evidence(tmp_path):
    results = _write(tmp_path / "cal_drc.out", "# Calibre DRC Results Database\n")
    summary = _write(tmp_path / "cal_drc.sum", "TOTAL RESULT COUNT = 0\n")
    parsed = parse_drc_results(results, summary_path=summary)
    assert parsed.status == "clean"
    assert parsed.total == 0
    assert parsed.checks == {}
    assert parsed.signoff_eligible is True
    assert parsed.summary is not None
    empty = _write(tmp_path / "empty.out", "")
    unknown = parse_drc_results(empty)
    assert unknown.status == "unknown"
    assert unknown.signoff_eligible is False


def test_drc_violations_bbox_counts_and_sample_bound(tmp_path):
    results = _write(
        tmp_path / "cal_drc.out",
        "M1.W.1\n"
        "3 3 2 Oct 23 19:58:01 2026\n"
        "Rule File Pathname: /tmp/drc.cal\n"
        "M1 width is >= 0.2um\n"
        "p 1 4\n"
        "0 0\n1 0\n1 1\n0 1\n"
        "p 2 4\n"
        "10 10\n11 10\n11 11\n10 11\n",
    )
    summary = _write(
        tmp_path / "cal_drc.sum",
        "RULECHECK M1.W.1 ............ TOTAL Result Count = 3\n"
        "TOTAL RESULT COUNT = 3\n",
    )
    parsed = parse_drc_results(
        results,
        summary_path=summary,
        max_violations=1,
        scope="candidate",
    )
    assert parsed.status == "violations"
    assert parsed.total == 3
    assert parsed.checks == {"M1.W.1": 3}
    assert len(parsed.violations) == 1
    assert parsed.violations[0].check == "M1.W.1"
    assert parsed.violations[0].bbox == ((0.0, 0.0), (1.0, 1.0))
    assert parsed.violations[0].count == 3
    assert parsed.violations[0].message == "M1 width is >= 0.2um"
    assert len(parsed.violations[0].details["sample_bboxes"]) == 2
    assert parsed.violations[0].details["samples_truncated"] is True
    assert parsed.to_dict()["violations"][0]["bbox"] == [[0.0, 0.0], [1.0, 1.0]]
    assert parsed.to_dict()["violations_truncated"] is True
    assert parsed.signoff_eligible is False


def test_drc_unrecognized_and_failed_outputs_are_not_clean(tmp_path):
    results = _write(tmp_path / "cal_drc.out", "some diagnostic text\n")
    summary = _write(tmp_path / "cal_drc.sum", "unrecognized summary\n")
    unknown = parse_drc_results(results, summary_path=summary)
    assert unknown.status == "unknown"
    failed_results = _write(
        tmp_path / "failed.out",
        "ERROR: DRC deck compilation failed\n",
    )
    failed = parse_drc_results(failed_results)
    assert failed.status == "failed"
    assert failed.signoff_eligible is False


def test_drc_summary_is_shared_with_direct_callers(tmp_path):
    summary = _write(
        tmp_path / "cal_drc.sum",
        'RULECHECK "A" RESULT COUNT = 2\n'
        'RULECHECK "A" RESULT COUNT = 7\n'
        "TOTAL RESULT COUNT = 9\n",
    )
    total, counts = parse_drc_summary(summary.read_text())
    assert total == 9
    assert counts == {"A": 7}


def test_drc_ascii_blocks_are_detected_without_summary(tmp_path):
    results = _write(
        tmp_path / "cal_drc.out",
        "GT_12\n"
        "1 1 2 Oct 23 19:58:01 2026\n"
        "Rule File Pathname: /tmp/drc.cal\n"
        "GT density is >= 14%\n"
        "p 1 4\n"
        "-2 -3\n7 -3\n7 5\n-2 5\n",
    )
    parsed = parse_drc_results(results)
    assert parsed.status == "violations"
    assert parsed.total == 1
    assert parsed.checks == {"GT_12": 1}
    assert parsed.violations[0].bbox == ((-2.0, -3.0), (7.0, 5.0))


def test_drc_messages_drop_calibre_control_characters(tmp_path):
    results = _write(
        tmp_path / "cal_drc.out",
        "CT_1\n"
        "1 1 2 Oct 23 19:58:01 2026\n"
        "Rule File Pathname: /tmp/drc.cal\n"
        "CT width\tis\n"
        "p 1 1\n"
        "0 0\n",
    )
    parsed = parse_drc_results(results)
    assert parsed.total == 1
    assert parsed.violations[0].message == "CT width is"


def test_drc_real_summary_total_line_is_read(tmp_path):
    results = _write(tmp_path / "cal_drc.out", "# empty\n")
    summary = _write(
        tmp_path / "cal_drc.sum",
        "TOTAL DRC Results Generated:     0\n",
    )
    parsed = parse_drc_results(results, summary_path=summary)
    assert parsed.status == "clean"
    assert parsed.total == 0


def test_drc_result_file_limit_is_checked_before_read(tmp_path):
    path = tmp_path / "oversized.out"
    with path.open("wb") as stream:
        stream.truncate(32 * 1024 * 1024 + 1)
    with pytest.raises(HubError) as failure:
        parse_drc_results(path)
    assert failure.value.code == "result_too_large"
    with pytest.raises(HubError) as missing:
        parse_drc_results(tmp_path / "missing.out")
    assert missing.value.code == "read_failed"


def test_lvs_correct_ignores_explicit_zero_count_lines(tmp_path):
    report = _write(
        tmp_path / "lvs.report",
        "LVS completed. CORRECT.\n"
        "Total errors: 0\n"
        "No mismatches.\n",
    )
    parsed = parse_lvs_report(report)
    assert parsed.status == "correct"
    assert parsed.counts == {}
    assert parsed.report is not None


def test_lvs_incorrect_unknown_and_failed_statuses(tmp_path):
    incorrect = _write(
        tmp_path / "incorrect.report",
        "LVS completed. INCORRECT.\n"
        "Nets: 4 4 2 1\n"
        "Total Inst: 3 3 1 2\n"
        "Mismatch: net A vs B\n",
    )
    svdb = _write(tmp_path / "svdb.manifest.json", "{}\n")
    parsed = parse_lvs_report(incorrect, svdb_path=svdb)
    assert parsed.status == "incorrect"
    assert parsed.counts["mismatch"] == 1
    assert parsed.counts["unmatched_layout"] == 3
    assert parsed.counts["unmatched_source"] == 3
    assert parsed.svdb is not None
    unknown = _write(tmp_path / "unknown.report", "Mismatch: details only\n")
    assert parse_lvs_report(unknown).status == "unknown"
    failed = _write(tmp_path / "failed.report", "FATAL: LVS failed to start\n")
    assert parse_lvs_report(failed).status == "failed"


def test_lvs_report_ascii_verdict_and_completion_log(tmp_path):
    report = _write(
        tmp_path / "lvs.report",
        "OVERALL COMPARISON RESULTS\n"
        "#     CORRECT     #\n"
        "Ports: 7 7 0 0\n"
        "Nets: 18 18 0 0\n",
    )
    log = _write(
        tmp_path / "callvs.log",
        "LVS completed. CORRECT. See report file\n",
    )
    parsed = parse_lvs_report(report, log_path=log)
    assert parsed.status == "correct"
    assert parsed.counts == {}
    assert parsed.log is not None
    without_log = parse_lvs_report(report)
    assert without_log.status == "correct"
    not_compared = _write(
        tmp_path / "not-compared.report",
        "OVERALL COMPARISON RESULTS\nNOT COMPARED\n",
    )
    parsed = parse_lvs_report(not_compared)
    assert parsed.status == "unknown"
    assert parsed.findings[0].category == "not_compared"
