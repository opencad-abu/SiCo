"""Calibre runner lifecycle, states, provenance and durable receipts."""

import shlex
import time

import pytest
from sico_calibre_fixture import (
    drc_script,
    lvs_script,
    make_flow,
    make_runner,
    wait_terminal,
)
from sico_datahub.calibre.receipts import JobReceipt
from sico_datahub.codec import artifact_ref
from sico_datahub.errors import HubError


def test_drc_clean_job_captures_provenance_and_artifacts(tmp_path):
    flow = make_flow(tmp_path)
    body = drc_script(
        flow.run_dir,
        summary="TOTAL RESULT COUNT = 0\n",
        results="# Calibre DRC Results Database\n",
    )
    runner = make_runner(tmp_path, drc_body=body)
    try:
        receipt = runner.submit(
            "drc", "drc-clean-1", str(flow.config), str(tmp_path)
        )
        assert receipt.state == "queued"
        final = wait_terminal(runner, "drc-clean-1")
        assert final.state == "clean"
        assert final.exit_code == 0
        assert final.result_snapshot is not None
        assert final.stdout is not None and final.stderr is not None
        stored, result = runner.result("drc-clean-1")
        assert stored.state == "clean"
        assert result["status"] == "clean"
        assert result["signoff_eligible"] is True
        assert result["provenance"]["run_ref"] == "drc-clean-1"
        assert result["provenance"]["tool_version"] == "Calibre 2026.3 fixture"
        assert result["provenance"]["top_cell"] == "inv"
        assert result["provenance"]["runset"]["path"] == str(flow.runset)
        assert len(result["provenance"]["dependencies_sha256"]) == 64
        assert result["provenance"]["candidate"]["path"] == str(flow.gds)
        assert result["results"]["path"].endswith("/db/drc/cal_drc.out")
        assert result["summary"]["path"].endswith("/log/cal_drc.sum")
        assert final.effective_deck is not None
        assert final.effective_config is not None
        assert result["provenance"]["effective_deck"]["path"] == final.effective_deck.path
        assert result["provenance"]["effective_config"]["path"] == final.effective_config.path
        assert result["provenance"]["options"]["deck_preparation"]["source"] == "svrf"
    finally:
        runner.close()


def test_selected_drc_check_is_not_reported_as_full_signoff(tmp_path):
    flow = make_flow(tmp_path, selected=True)
    body = drc_script(
        flow.run_dir,
        summary="TOTAL RESULT COUNT = 0\n",
        results="# Calibre DRC Results Database\n",
    )
    runner = make_runner(tmp_path, drc_body=body)
    try:
        runner.submit("drc", "drc-selected-1", str(flow.config), str(tmp_path))
        assert wait_terminal(runner, "drc-selected-1").state == "clean"
        _stored, result = runner.result("drc-selected-1")
        assert result["scope"] == "candidate"
        assert result["signoff_eligible"] is False
    finally:
        runner.close()


def test_nonzero_drc_exit_downgrades_parsed_clean_result(tmp_path):
    flow = make_flow(tmp_path)
    body = drc_script(
        flow.run_dir,
        summary="TOTAL RESULT COUNT = 0\n",
        results="# Calibre DRC Results Database\n",
        exit_code=1,
    )
    runner = make_runner(tmp_path, drc_body=body)
    try:
        runner.submit("drc", "drc-failed-1", str(flow.config), str(tmp_path))
        final = wait_terminal(runner, "drc-failed-1")
        assert final.state == "failed"
        assert "code 1" in final.message
        _stored, result = runner.result("drc-failed-1")
        assert result["status"] == "failed"
        assert result["signoff_eligible"] is False
    finally:
        runner.close()


def test_timeout_is_unknown_and_has_no_result_replay(tmp_path):
    flow = make_flow(tmp_path)
    runner = make_runner(
        tmp_path,
        drc_body=drc_script(flow.run_dir, delay=5),
        timeout=0.2,
    )
    try:
        runner.submit("drc", "drc-timeout-1", str(flow.config), str(tmp_path))
        final = wait_terminal(runner, "drc-timeout-1", timeout=5)
        assert final.state == "unknown"
        assert "timed out" in final.message
        with pytest.raises(HubError) as failure:
            runner.result("drc-timeout-1")
        assert failure.value.code == "source_not_captured"
        with pytest.raises(HubError) as duplicate:
            runner.submit("drc", "drc-timeout-1", str(flow.config), str(tmp_path))
        assert duplicate.value.code == "stale_context"
    finally:
        runner.close()


def test_lvs_incorrect_is_a_valid_terminal_state(tmp_path):
    flow = make_flow(tmp_path, kind="lvs")
    body = lvs_script(
        flow.run_dir,
        report="LVS completed. INCORRECT.\nMismatch: net A vs B\n",
        exit_code=1,
    )
    runner = make_runner(tmp_path, lvs_body=body)
    try:
        runner.submit("lvs", "lvs-incorrect-1", str(flow.config), str(tmp_path))
        final = wait_terminal(runner, "lvs-incorrect-1")
        assert final.state == "incorrect"
        assert final.exit_code == 1
        assert final.svdb is not None
        _stored, result = runner.result("lvs-incorrect-1")
        assert result["status"] == "incorrect"
        assert result["counts"]["mismatch"] == 1
        assert result["svdb"]["path"].endswith("svdb-manifest.json")
    finally:
        runner.close()


def test_runner_rejects_work_root_escape_and_result_before_completion(tmp_path):
    flow = make_flow(tmp_path)
    runner = make_runner(
        tmp_path,
        drc_body=drc_script(flow.run_dir, delay=1),
        timeout=5,
    )
    try:
        other = tmp_path / "other"
        other.mkdir()
        with pytest.raises(HubError) as mismatch:
            runner.submit("drc", "drc-other-root", str(flow.config), str(other))
        assert mismatch.value.code == "invalid_arguments"
        runner.submit("drc", "drc-pending-1", str(flow.config), str(tmp_path))
        with pytest.raises(HubError) as pending:
            runner.result("drc-pending-1")
        assert pending.value.code == "invalid_arguments"
        wait_terminal(runner, "drc-pending-1")
    finally:
        runner.close()


def test_restarted_runner_reconciles_unowned_running_job(tmp_path):
    flow = make_flow(tmp_path)
    runner = make_runner(tmp_path)
    receipt = JobReceipt(
        run_ref="drc-orphan-1",
        kind="drc",
        state="running",
        config=artifact_ref(flow.config),
        work_root=str(tmp_path),
        created_at="2026-10-23T00:00:00+00:00",
        started_at="2026-10-23T00:00:01+00:00",
    )
    runner.store.create(receipt)
    try:
        reconciled = runner.status("drc-orphan-1")
        assert reconciled.state == "unknown"
        assert "restart" in reconciled.message
        assert runner.store.load("drc-orphan-1").state == "unknown"
    finally:
        runner.close()


def test_cancel_before_start_marks_queued_job_cancelled_and_is_idempotent(tmp_path):
    flow = make_flow(tmp_path)
    runner = make_runner(tmp_path)
    runner.store.create(
        JobReceipt(
            run_ref="drc-cancel-queued",
            kind="drc",
            state="queued",
            config=artifact_ref(flow.config),
            work_root=str(tmp_path),
            created_at="2026-10-24T00:00:00+00:00",
        )
    )
    try:
        first = runner.cancel("drc-cancel-queued")
        assert first.state == "cancelled"
        assert first.terminal is True
        assert first.message == "Calibre job cancelled before start"
        assert first.finished_at
        second = runner.cancel("drc-cancel-queued")
        assert second == first
        assert runner.status("drc-cancel-queued").state == "cancelled"
    finally:
        runner.close()


def test_cancel_stops_an_active_job_and_persists_cancelled_state(tmp_path):
    flow = make_flow(tmp_path)
    marker = tmp_path / "drc-started"
    body = (
        f"printf started > {shlex.quote(str(marker))}\n"
        "sleep 30\n"
    )
    runner = make_runner(tmp_path, drc_body=body)
    try:
        runner.submit("drc", "drc-cancel-active", str(flow.config), str(tmp_path))
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not marker.is_file():
            time.sleep(0.02)
        assert marker.is_file()
        final = runner.cancel("drc-cancel-active")
        assert final.state == "cancelled"
        assert final.terminal is True
        assert final.message == "Calibre job cancelled"
        assert final.started_at
        assert final.finished_at
        assert final.pid is not None
        assert runner.status("drc-cancel-active").state == "cancelled"
        assert runner.cancel("drc-cancel-active").state == "cancelled"
    finally:
        runner.close()


def test_runner_uses_private_derived_config(tmp_path):
    flow = make_flow(tmp_path)
    recorded = tmp_path / "recorded-config"
    body = (
        f'printf "%s" "$2" > {shlex.quote(str(recorded))}\n'
        + drc_script(
            flow.run_dir,
            summary="TOTAL RESULT COUNT = 0\n",
            results="# Calibre DRC Results Database\n",
        )
    )
    runner = make_runner(tmp_path, drc_body=body)
    try:
        runner.submit("drc", "drc-derived-1", str(flow.config), str(tmp_path))
        final = wait_terminal(runner, "drc-derived-1")
        assert final.state == "clean"
        assert final.effective_config is not None
        assert recorded.read_text(encoding="utf-8") == final.effective_config.path
        assert "/.sico_datahub/calibre/configs/drc-derived-1.toml" in recorded.read_text(
            encoding="utf-8"
        )
        assert recorded.read_text(encoding="utf-8") != str(flow.config)
    finally:
        runner.close()
