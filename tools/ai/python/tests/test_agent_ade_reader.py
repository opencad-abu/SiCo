"""Real IC231 SKILL queries against isolated setup, with no simulation launch."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

CAD = Path(__file__).absolute().parents[3]
IC = Path("/software/eda/cadence/installs/IC23.10.130/tools/dfII/bin/virtuoso")
pytestmark = pytest.mark.skipif(
    os.environ.get("SICO_IC_TESTS") != "1", reason="Opt-in ADE query test"
)


def test_setup_selection_truncation_history_and_dispatch_validation(tmp_path):
    lines = [
        f"load({json.dumps(str(CAD / 'sico/skill++/SICO.ils'))})",
        f'ddCreateLib("AgentAdeRead" {json.dumps(str(tmp_path / "library"))})',
        'cv=dbOpenCellViewByType("AgentAdeRead" "tb" "schematic" "schematic" "w")',
        "dbSave(cv) dbClose(cv)",
        'session=maeOpenSetup("AgentAdeRead" "tb" "maestro" ?application "Assembler")',
        'foreach(name list("nominal" "secondary")',
        '  maeCreateTest(name ?lib "AgentAdeRead" ?cell "tb" ?view "schematic"',
        '    ?simulator "spectre" ?session session))',
        'maeAddOutput("gain" "nominal" ?expr "1 + 2" ?session session)',
        'maeSetVar("VDD" "1.2" ?session session)',
        'binding=makeTable("readerBinding" nil) binding[\'adeSession]=session',
        'printf("READ_ALL %s\\n" sicoAdeSetupJson(binding))',
        'binding[\'testName]="nominal"',
        'printf("READ_SELECTED %s\\n" sicoAdeSetupJson(binding))',
        'printf("READ_EMPTY_HISTORY %s\\n" sicoAdeHistoryJson(binding))',
        'for(index 1 35 maeAddOutput(sprintf(nil "output_%d" index) "nominal" '
        '?expr "3" ?session session))',
        'printf("READ_LIMIT %s\\n" sicoAdeSetupJson(binding))',
        "sdb=axlGetMainSetupDB(session)",
        'history=axlPutHistoryEntry(sdb "AgentSynthetic.1")',
        "binding['historyName]=\"AgentSynthetic.1\" binding['historyHandle]=history",
        'printf("READ_SYNTHETIC_HISTORY %s\\n" sicoAdeHistoryJson(binding))',
        "binding['window]=-1",
        'response=sicoReadReply(binding "0123456789abcdef0123456789abcdef read_ade_setup")',
        'printf("READ_STALE %s" response)',
        'when(sicoReadReply(binding "0123456789abcdef0123456789abcdef eval_skill")',
        '  error("Unknown method was dispatched"))',
        'when(sicoReadReply(binding "0123456789abcdef0123456789abcdef read_ade_setup injected")',
        '  error("Unknown arguments were dispatched"))',
        "maeSaveSetup(?session session) maeCloseSession(?session session ?forceClose t)",
        'printf("ADE_READ_PASS\\n") exit()',
    ]
    source = tmp_path / "read.il"
    source.write_text("\n".join(lines) + "\n")
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("QT_")
        and k
        not in {
            "LD_LIBRARY_PATH",
            "LD_PRELOAD",
            "PYTHONHOME",
            "PYTHONPATH",
            "SICO_ROOT",
        }
    }
    with (tmp_path / "stdout.log").open("w") as out:
        process = subprocess.Popen(
            [
                str(IC),
                "-nograph",
                "-nocdsinit",
                "-log",
                str(tmp_path / "CDS.log"),
                "-restore",
                str(source),
            ],
            cwd=tmp_path,
            env=env,
            stdout=out,
            stderr=subprocess.STDOUT,
        )
        try:
            process.wait(timeout=35)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
    log = (tmp_path / "stdout.log").read_text()
    assert "ADE_READ_PASS" in log and "*Error*" not in log, log
    records = {
        line.split(" ", 1)[0]: json.loads(line.split(" ", 1)[1])
        for line in log.splitlines()
        if line.startswith("READ_")
    }
    assert records["READ_ALL"]["session_test_count"] == 2
    setup = records["READ_SELECTED"]
    assert setup["selected_test_count"] == 1 and len(setup["tests"]) == 1
    assert setup["tests"][0]["outputs"] == [{"name": "gain", "expression": "(1 + 2)", "signal": ""}]
    assert setup["global_variables"] == [{"name": "VDD", "value": "1.2"}]
    assert setup["data_basis"] == "current_setup" and setup["truncated"] is False
    assert records["READ_EMPTY_HISTORY"]["histories"] == []
    assert records["READ_LIMIT"]["truncated"] is True
    assert records["READ_LIMIT"]["tests"][0]["output_count"] == 36
    assert len(records["READ_LIMIT"]["tests"][0]["outputs"]) == 32
    history = records["READ_SYNTHETIC_HISTORY"]
    assert history["scope"] == "history" and history["histories"][0]["name"] == "AgentSynthetic.1"
    assert records["READ_STALE"]["ok"] is False and records["READ_STALE"]["code"] == "stale_context"
