from __future__ import annotations

import json
from pathlib import Path

import pytest

from cadai.candidate_ledger import CandidateLedger, CandidateLedgerError


def test_candidate_ledger_binds_feedback_to_authoritative_source_and_restores(tmp_path: Path) -> None:
    spool = tmp_path / "spool"
    spool.mkdir()
    ledger = CandidateLedger(spool)
    generation = "a" * 64
    record = ledger.submit(
        {
            "source": "module top; endmodule",
            "module": "top",
            "language": "systemverilog",
            "source_generation": generation,
        }
    )
    candidate = record["candidate_sha256"]
    recipe = {
        "aivw": {
            "status": "PASS",
            "run_id": "run-1",
            "details": {
                "gate_results": {
                    "snapshot": {
                        "status": "PASS",
                        "outputs": {"source_generation": generation},
                    }
                }
            },
        }
    }
    updated = ledger.attach_feedback(candidate, recipe)
    assert updated["feedback"]["status"] == "PASS"
    restored = CandidateLedger(spool)
    assert restored.get(candidate)["feedback"]["evidence"]["source_generation"] == generation


def test_candidate_ledger_rejects_stale_feedback_and_tampered_artifact(tmp_path: Path) -> None:
    spool = tmp_path / "spool"
    spool.mkdir()
    ledger = CandidateLedger(spool)
    generation = "a" * 64
    record = ledger.submit(
        {
            "source": "module top; endmodule",
            "module": "top",
            "language": "systemverilog",
            "source_generation": generation,
        }
    )
    with pytest.raises(CandidateLedgerError, match="does not match candidate"):
        ledger.attach_feedback(
            record["candidate_sha256"],
            {
                "aivw": {
                    "status": "PASS",
                    "details": {
                        "gate_results": {
                            "snapshot": {
                                "status": "PASS",
                                "outputs": {"source_generation": "b" * 64},
                            }
                        }
                    },
                }
            },
        )
    artifact = spool / record["artifact"]["name"]
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    payload["source"] = "module top; endmodule // changed"
    artifact.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CandidateLedgerError, match="source_sha256"):
        CandidateLedger(spool)


def test_candidate_ledger_requires_authoritative_feedback_and_rejects_unknown_candidate(
    tmp_path: Path,
) -> None:
    spool = tmp_path / "spool"
    spool.mkdir()
    ledger = CandidateLedger(spool)
    generation = "a" * 64
    record = ledger.submit(
        {
            "source": "module top; endmodule",
            "module": "top",
            "language": "systemverilog",
            "source_generation": generation,
        }
    )
    with pytest.raises(CandidateLedgerError, match="does not match candidate"):
        ledger.attach_feedback(
            record["candidate_sha256"],
            {"aivw": {"status": "PASS", "details": {"gate_results": {}}}},
        )
    with pytest.raises(CandidateLedgerError, match="not present"):
        ledger.attach_feedback("b" * 64, {"aivw": {"status": "BLOCKED"}})
