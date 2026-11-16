"""Debt is a bounded pending obligation, never a green static check."""

from copy import deepcopy
from datetime import date

import pytest

from engineering_debt import assess
from engineering_findings import a_findings, metric

TODAY = date(2026, 10, 15)


def fixture():
    finding = metric("a", "size:pkg/large.py", "large file", 520, 500)
    entry = dict(id=finding["id"], ceiling=520, owner="pkg", reason="existing", remediation="EQ-08",
                 exit_condition="split and validate", expires="2026-10-22")
    return finding, dict(schema_version=1, established_base="base", entries=[entry])


def run(findings, payload, previous):
    return assess(findings, payload, previous=previous, base_commit="base", today=TODAY)


def test_initial_registration_is_bound_to_audited_base_and_keeps_debt_visible():
    finding, payload = fixture()
    result = run([finding], payload, None)
    assert not result["errors"]
    assert result["accepted_debt"][0]["allowance"]["remediation"] == "EQ-08"
    payload["established_base"] = "another"
    assert "exact audited base" in run([finding], payload, None)["errors"][0]


@pytest.mark.parametrize("field,value,expected", [
    ("owner", "", "requires owner"),
    ("reason", "", "requires reason"),
    ("exit_condition", "", "requires exit_condition"),
    ("expires", "2026-10-15", "expired"),
    ("expires", "tomorrow", "invalid debt expiration"),
    ("expires", "2026-10-23", "extending debt"),
    ("ceiling", 521, "increasing debt"),
    ("ceiling", True, "numeric debt"),
])
def test_invalid_or_relaxed_allowance_fails(field, value, expected):
    finding, previous = fixture()
    current = deepcopy(previous)
    current["entries"][0][field] = value
    result = run([finding], current, previous)
    assert any(expected in error for error in result["errors"])
    assert not result["accepted_debt"]


def test_new_debt_is_forbidden_even_with_complete_metadata():
    finding, previous = fixture()
    current = deepcopy(previous)
    current["entries"][0]["id"] = finding["id"] = "a:size:other.py"
    assert any("new debt" in error for error in run([finding], current, previous)["errors"])


def test_growth_and_reduction_cannot_hide_behind_a_total_error_count():
    finding, previous = fixture()
    finding["value"] = 521
    assert run([finding], previous, previous)["errors"]
    finding["value"] = 510
    assert any("lower debt ceiling" in error for error in run([finding], previous, previous)["errors"])
    current = deepcopy(previous)
    current["entries"][0]["ceiling"] = 510
    assert not run([finding], current, previous)["errors"]
    replacement = dict(finding, id="a:size:other.py")
    result = run([replacement], current, previous)
    assert result["unregistered"] == [replacement]
    assert any("remove resolved" in error for error in result["errors"])


def test_resolution_requires_removing_the_allowance_and_cannot_be_reintroduced():
    finding, previous = fixture()
    assert any("remove resolved" in error for error in run([], previous, previous)["errors"])
    current = dict(previous, entries=[])
    result = run([], current, previous)
    assert not result["errors"] and result["resolved"] == [finding["id"]]
    assert any("new debt" in error for error in run([finding], previous, current)["errors"])


def test_surface_change_requires_review_even_if_the_allowance_is_rehashed():
    finding = dict(id="reviews:pkg/api.py", gate="reviews", fingerprint="old", message="stale")
    _, previous = fixture()
    previous["entries"][0].pop("ceiling")
    previous["entries"][0].update(id=finding["id"], fingerprint="old")
    current = deepcopy(previous)
    finding["fingerprint"] = current["entries"][0]["fingerprint"] = "new"
    assert any("replacing debt evidence" in error for error in run([finding], current, previous)["errors"])


def test_duplicate_or_missing_ids_are_configuration_errors():
    finding, payload = fixture()
    payload["entries"] *= 2
    with pytest.raises(ValueError, match="duplicate"):
        run([finding], payload, None)


def test_pending_review_binds_surface_but_not_ordinary_implementation_edits(tmp_path):
    path = tmp_path / "api.py"
    path.write_text('"""Legacy adapter."""\ndef call(a): return a\n')
    report = dict(errors=[], checks={}, compatibility_candidates=[dict(path="api.py", review_state="stale")])
    original = a_findings(tmp_path, report)[0]
    path.write_text('"""Legacy adapter."""\ndef call(a): return a + 1\n')
    assert a_findings(tmp_path, report)[0]["fingerprint"] == original["fingerprint"]
    path.write_text('"""Legacy adapter."""\ndef call(a, b): return a + b\n')
    assert a_findings(tmp_path, report)[0]["fingerprint"] != original["fingerprint"]
