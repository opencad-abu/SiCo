"""Bounded Calibre result paging and bridge route registration."""

from sico_calibre_fixture import make_runner
from sico_datahub.calibre.handlers import _result_payload, _size, calibre_handlers
from sico_datahub.calibre.receipts import JobReceipt
from sico_datahub.codec import artifact_ref
from sico_datahub.limits import MAX_PAYLOAD_BYTES


def _receipt(tmp_path, kind="drc", state="violations"):
    config = tmp_path / "flow.toml"
    config.write_text("[run]\n", encoding="utf-8")
    return JobReceipt(
        run_ref=kind + "-page-1",
        kind=kind,
        state=state,
        config=artifact_ref(config),
        work_root=str(tmp_path),
        created_at="2026-10-23T00:00:00+00:00",
    )


def test_drc_result_paging_stays_inside_the_payload_limit(tmp_path):
    receipt = _receipt(tmp_path)
    violations = [
        {
            "check": f"C{index:03d}",
            "message": "x" * 4096,
            "severity": "error",
            "cell": "inv",
            "layer": "M1",
            "bbox": [[0, 0], [1, 1]],
            "count": 1,
            "details": {"source": "calibre-ascii"},
        }
        for index in range(120)
    ]
    result = {
        "status": "violations",
        "total": 1000,
        "checks": {f"C{index:03d}": index for index in range(100)},
        "violations": violations,
        "results": None,
        "summary": None,
        "provenance": None,
        "scope": "full",
        "signoff_eligible": False,
        "violations_truncated": True,
    }
    payload, page = _result_payload(receipt, result, "drc", 0, 50)
    assert _size(payload) <= MAX_PAYLOAD_BYTES
    assert page.total == 120
    assert page.truncated is True
    assert page.next is not None and page.next > 0
    assert 0 < len(payload["result"]["violations"]) < 50
    assert len(payload["result"]["checks"]) == 64
    assert payload["result"]["checks_truncated"] is True
    assert payload["result"]["violations_truncated"] is True
    second, second_page = _result_payload(receipt, result, "drc", page.next, 50)
    assert second_page.offset == page.next
    assert second["result"]["violations"][0]["check"] == violations[page.next]["check"]


def test_lvs_result_paging_uses_findings_and_counts(tmp_path):
    receipt = _receipt(tmp_path, kind="lvs", state="incorrect")
    findings = [
        {"category": "mismatch", "message": "m" * 4096, "cell": "inv",
         "net": f"N{index}", "count": 1}
        for index in range(75)
    ]
    result = {
        "status": "incorrect",
        "counts": {"mismatch": 75},
        "findings": findings,
        "report": None,
        "svdb": None,
        "provenance": None,
        "findings_truncated": False,
    }
    payload, page = _result_payload(receipt, result, "lvs", 0, 50)
    assert _size(payload) <= MAX_PAYLOAD_BYTES
    assert page.total == 75
    assert page.truncated is True
    assert 0 < len(payload["result"]["findings"]) < 50


def test_handler_map_registers_catalog_and_all_job_actions(tmp_path):
    runner = make_runner(tmp_path)
    try:
        handlers = calibre_handlers(runner)
    finally:
        runner.close()
    for contract in ("sico.calibre.drc.v1", "sico.calibre.lvs.v1"):
        assert (contract, "probe") in handlers
        assert (contract, "submit") in handlers
        assert (contract, "status") in handlers
        assert (contract, "result") in handlers
    assert ("sico.calibre.drc.v1", "catalog") in handlers
    assert ("sico.calibre.drc.v1", "check") in handlers
    assert ("sico.calibre.lvs.v1", "check") in handlers
    assert ("sico.calibre.lvs.v1", "export") in handlers
