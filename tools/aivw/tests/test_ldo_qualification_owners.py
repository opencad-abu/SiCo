"""Qualification boundaries after separating source and evidence ownership."""

from copy import deepcopy
import json
import stat

import pytest

from aivworkbench import ldo_qualification as compat
from aivworkbench.ldo_qualification_aggregate import aggregate_ldo_qualification
from aivworkbench.ldo_qualification_evidence import validate_topology_evidence
from aivworkbench.ldo_qualification_holdout import validate_holdout_blindness
from aivworkbench.ldo_qualification_repeat import compare_repeat_runs
from aivworkbench.ldo_qualification_report import write_ldo_qualification_report
from aivworkbench.ldo_snapshot_authentication import validate_authenticated_snapshot


DIGEST = "a" * 64


def evidence(topology):
    return {
        "schema_version": 1, "topology": topology, "source_generation": DIGEST,
        "gates": {"G%d" % i: {"status": "PASS"} for i in range(8)},
        "candidate_sha256": DIGEST, "interface_digest": DIGEST,
        "artifacts": [{"path": "result.json", "sha256": DIGEST, "size": 2}],
        "manifest_pair": {"status": "PASS", "artifacts_verified": True, "run_status": "PASS"},
        "holdout": {"locked": True, "public_values_included": False}, "run_id": "run-1",
    }


def test_compatibility_uses_the_source_evidence_and_report_owners():
    assert compat.validate_topology_evidence is validate_topology_evidence
    assert compat.write_ldo_qualification_report is write_ldo_qualification_report
    assert compat.aggregate_ldo_qualification is aggregate_ldo_qualification


@pytest.mark.parametrize("missing", ["formal_saved_state", "double_read_equal", "source_generation"])
def test_authentication_rejects_missing_controller_evidence(missing):
    record = {"schema_version": 1, "status": "PASS", "authority": "controller-owned-virtuoso-read-only",
              "formal_saved_state": True, "double_read_equal": True, "source_generation": DIGEST,
              "target": {"library": "amsLDO", "cells": ["LDO_MASTER", "LDO_AON"]}}
    assert validate_authenticated_snapshot(record, source_generation=DIGEST, library="amsLDO")["authenticated"]
    record.pop(missing)
    result = validate_authenticated_snapshot(record, source_generation=DIGEST, library="amsLDO")
    assert result["status"] == "BLOCKED_INPUT" and not result["authenticated"]


@pytest.mark.parametrize(
    "authority", ["authenticated-cdns-ipc-read-only", "authenticated-virtuoso-ipc-read-only"]
)
def test_authentication_accepts_current_and_pre_rename_authority(authority):
    record = {"schema_version": 1, "status": "PASS", "authority": authority,
              "formal_saved_state": True, "double_read_equal": True, "source_generation": DIGEST,
              "target": {"library": "amsLDO", "cells": ["LDO_MASTER", "LDO_AON"]}}
    assert validate_authenticated_snapshot(record, source_generation=DIGEST, library="amsLDO")["authenticated"]


def test_authentication_rejects_an_unknown_authority():
    record = {"schema_version": 1, "status": "PASS", "authority": "untrusted-read-only",
              "formal_saved_state": True, "double_read_equal": True, "source_generation": DIGEST,
              "target": {"library": "amsLDO", "cells": ["LDO_MASTER", "LDO_AON"]}}
    result = validate_authenticated_snapshot(record, source_generation=DIGEST, library="amsLDO")
    assert result["status"] == "BLOCKED_INPUT" and not result["authenticated"]


def test_family_gate_keeps_authentication_and_failed_gate_precedence():
    snapshot = {"source_generation": DIGEST, "status": "PASS", "authenticated": True}
    entries = {name: evidence(name) for name in ("LDO_MASTER", "LDO_AON")}
    original = deepcopy(entries)
    result = aggregate_ldo_qualification(entries, source_snapshot=snapshot)
    assert result["status"] == "NEEDS_MORE_EVIDENCE"
    assert entries == original
    entries["LDO_AON"]["gates"]["G3"]["status"] = "FAIL_SPECTRE"
    assert aggregate_ldo_qualification(entries, source_snapshot=snapshot)["status"] == "FAIL_QUALIFICATION"
    snapshot["authenticated"] = False
    assert aggregate_ldo_qualification(entries, source_snapshot=snapshot)["status"] == "BLOCKED_ENVIRONMENT"


def test_repeat_requires_unique_runs_and_equal_qualification_keys():
    first = {"status": "PASS", "run_id": "run-a", "source_generation": DIGEST,
             "candidate_sha256": DIGEST, "gate_result_sha256": DIGEST}
    second = {**first, "run_id": "run-b"}
    assert compare_repeat_runs([first, second])["status"] == "PASS"
    assert compare_repeat_runs([first, first])["status"] == "BLOCKED_INPUT"
    second["candidate_sha256"] = "b" * 64
    assert compare_repeat_runs([first, second])["status"] == "FAIL_REPEAT"


def test_holdout_findings_do_not_serialize_hidden_payloads():
    hidden = {"case_ids": ["private-case-x"], "values": [791.123]}
    public = {"holdout": {"locked": True, "public_values_included": False}, "name": "private-case-x"}
    result = validate_holdout_blindness(public, hidden_holdout=hidden)
    assert result["status"] == "BLOCKED_INPUT"
    assert "private-case-x" not in json.dumps(result) and "791.123" not in json.dumps(result)
    assert result["summary"]["identifier_count"] == 1


def test_report_is_private_immutable_and_rejects_symlink_targets(tmp_path):
    target = tmp_path / "qualification.json"
    report = {"status": "NEEDS_MORE_EVIDENCE", "findings": ["missing repeat"]}
    assert write_ldo_qualification_report(report, target) == target
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    with pytest.raises(ValueError, match="replace"):
        write_ldo_qualification_report({}, target)
    alias = tmp_path / "alias.json"
    alias.symlink_to(target)
    with pytest.raises(ValueError, match="unsafe"):
        write_ldo_qualification_report({}, alias, overwrite=True)
    assert json.loads(target.read_text()) == report
