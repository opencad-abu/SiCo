"""Source eligibility reports must distinguish failure, debt and execution gaps."""

from datetime import date
import json
import subprocess

import pytest

import engineering_gate
from engineering_bootstrap import compare_initial
from engineering_findings import metric
from engineering_push import check_updates
from utility.tests.gate_fixtures import commit, write
from test_mcp_catalog_budget import baseline


def reports(repo):
    catalog, budget = baseline()
    a = dict(passed=True, errors=[], checks={}, compatibility_candidates=[])
    debt = dict(schema_version=1, established_base="fixture", entries=[])
    write(repo, engineering_gate.BUDGET, json.dumps(budget))
    write(repo, engineering_gate.DEBT, json.dumps(debt))
    commit(repo, "tools")
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo).decode().strip()
    return a, catalog, budget, base


def test_strict_pass_and_unregistered_failure_are_distinct(repo):
    a, catalog, budget, base = reports(repo)
    result = engineering_gate.evaluate(repo, a, catalog, budget, dict(passed=True, errors=[]), base_commit=base)
    assert result["passed"] and result["eligible"] and result["status"] == "passed"
    assert result["not_run"]["runtime_release"]
    a.update(passed=False, errors=["new hard error"])
    result = engineering_gate.evaluate(repo, a, catalog, budget, dict(passed=True, errors=[]), base_commit=base)
    assert not result["passed"] and not result["eligible"] and result["status"] == "failed"
    assert result["stages"]["a"]["status"] == "failed"


def test_accepted_debt_is_never_labelled_passed(repo):
    a, catalog, budget, _ = reports(repo)
    a.update(passed=False, errors=["unbaselined file exceeds 500 lines: pkg/file.py (510; tracked)"])
    path = repo / engineering_gate.DEBT
    payload = json.loads(path.read_text())
    payload["entries"] = [dict(id="a:size:pkg/file.py", ceiling=510, owner="pkg", reason="existing", remediation="EQ-08",
                               exit_condition="split", expires="2026-10-22")]
    path.write_text(json.dumps(payload))
    commit(repo, engineering_gate.DEBT)
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo).decode().strip()
    result = engineering_gate.evaluate(repo, a, catalog, budget, dict(passed=True, errors=[]),
                                       base_commit=base, today=date(2026, 10, 15))
    assert result["eligible"] and not result["passed"]
    assert result["status"] == "accepted_debt"
    expired = engineering_gate.evaluate(repo, a, catalog, budget, dict(passed=True, errors=[]),
                                        base_commit=base, today=date(2026, 10, 22))
    assert not expired["eligible"] and expired["stages"]["a"]["status"] == "failed"


def test_failed_execution_overwrites_previous_success_receipt(tmp_path, monkeypatch):
    output = tmp_path / "report"
    output.mkdir()
    (output / "summary.json").write_text('{"status":"passed","eligible":true}')
    def fail(*args):
        raise ValueError("catalog cannot execute")
    monkeypatch.setattr(engineering_gate, "run", fail)
    assert engineering_gate.main(["--base-ref", "HEAD", "--output", str(output)]) == 2
    receipt = json.loads((output / "summary.json").read_text())
    assert receipt["status"] == "error" and not receipt["eligible"]


def test_source_identity_covers_untracked_code_and_json_controls(repo):
    write(repo, "pkg/main.py", "pass\n")
    write(repo, "tools/utility/policy.json", '{"ceiling":1}')
    before = engineering_gate.source_identity(repo)
    write(repo, "pkg/main.py", "raise Exception()\n")
    assert engineering_gate.source_identity(repo) != before
    before = engineering_gate.source_identity(repo)
    write(repo, "tools/utility/policy.json", '{"ceiling":2}')
    assert engineering_gate.source_identity(repo) != before
    before = engineering_gate.source_identity(repo)
    write(repo, ".githooks/pre-push", "exit 0\n")
    assert engineering_gate.source_identity(repo) != before


def test_initial_registry_cannot_hide_new_or_expanded_debt():
    original = metric("a", "size:old.py", "large", 520, 500)
    assert not compare_initial([dict(id=original["id"], ceiling=510)], [original])
    assert compare_initial([dict(id="a:size:new.py", ceiling=510)], [original])
    assert compare_initial([dict(id=original["id"], ceiling=521)], [original])
    original = dict(id="reviews:api.py", fingerprint="old")
    assert compare_initial([dict(id=original["id"], fingerprint="new")], [original])


def test_main_push_uses_committed_snapshot_and_preserves_worktree(repo):
    gate = "tools/utility/engineering_gate.py"
    write(repo, gate, 'import sys\nfrom pathlib import Path\nsys.exit(0 if Path("allow").read_text() == "yes" else 1)\n')
    write(repo, "allow", "yes")
    commit(repo, gate, "allow")
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo).decode().strip()
    write(repo, "note", "candidate")
    commit(repo, "note")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo).decode().strip()
    write(repo, "allow", "dirty worktree says no")
    check_updates(repo, [f"refs/heads/main {head} refs/heads/main {base}"])
    assert (repo / "allow").read_text() == "dirty worktree says no"
    # A bad pushed commit cannot borrow an uncommitted repair.
    write(repo, "allow", "no")
    commit(repo, "allow")
    bad = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo).decode().strip()
    write(repo, "allow", "yes")
    with pytest.raises(ValueError, match="push rejected"):
        check_updates(repo, [f"refs/heads/main {bad} refs/heads/main {head}"])
    assert (repo / "allow").read_text() == "yes"
    assert not list((repo / ".cad/engineering-push").glob("*/checkout"))
