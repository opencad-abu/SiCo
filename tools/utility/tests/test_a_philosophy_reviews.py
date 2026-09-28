"""Partial compatibility registrations never imply completed module review."""

import json

import pytest

from utility.a_philosophy_compat import compatibility_candidates, load_compatibility
from utility.a_philosophy_exports import surface_digest
from utility.a_philosophy_reviews import review_candidates
from utility.a_philosophy_sources import source_rows
from conftest import commit, write


@pytest.fixture
def reviewed(repo):
    text = '"""Compatibility facade."""\nfrom .owner import Result\n'
    write(repo, "pkg/api.py", text)
    write(repo, "pkg/owner.py", "class Result: pass\n")
    commit(repo, "pkg")
    records = [dict(legacy="api.Result", source="pkg/api.py")]
    review = dict(path="pkg/api.py", disposition="compatibility", records=["api.Result"],
                  rationale="Only explicit imports are retained.", evidence=["pkg/owner.py"],
                  surface_sha256=surface_digest(text))
    candidates = compatibility_candidates(repo, source_rows(repo)[0], records)
    return repo, candidates, records, dict(schema_version=1, reviews=[review])


def test_registration_is_not_a_review(reviewed):
    root, candidates, records, payload = reviewed
    assert candidates == [dict(path="pkg/api.py", has_registration=True)]
    errors, warnings, reports = review_candidates(root, candidates, records, {**payload, "reviews": []})
    assert not errors
    assert warnings and reports[0]["review_state"] == "unreviewed"


def test_new_export_reopens_review_even_with_existing_records(reviewed):
    root, candidates, records, payload = reviewed
    assert review_candidates(root, candidates, records, payload)[2][0]["review_state"] == "reviewed"
    path = root / "pkg/api.py"
    path.write_text(path.read_text() + "Second = Result\n")
    errors, warnings, reports = review_candidates(root, candidates, records, payload)
    assert not errors and warnings and reports[0]["review_state"] == "stale"


@pytest.mark.parametrize("change", [
    {"records": ["missing"]}, {"records": []}, {"evidence": ["absent.py"]},
    {"rationale": ""}, {"disposition": "ignore"},
    {"records": [{}]},
])
def test_invalid_decisions_fail(reviewed, change):
    root, candidates, records, payload = reviewed
    payload["reviews"][0].update(change)
    assert review_candidates(root, candidates, records, payload)[0]


def test_valid_owner_can_be_classified_without_a_fake_retirement(reviewed):
    root, candidates, records, payload = reviewed
    payload["reviews"][0].update(disposition="canonical", records=[], rationale="Current domain adapter.")
    assert review_candidates(root, candidates, records, payload)[:2] == ([], [])


def test_duplicate_review_rejected(reviewed):
    root, candidates, records, payload = reviewed
    payload["reviews"] *= 2
    assert any("duplicate" in e for e in review_candidates(root, candidates, records, payload)[0])


def test_partitioned_registry_rejects_missing_and_duplicate_includes(repo):
    path = "tools/utility/a_philosophy_compatibility.json"
    part = "tools/utility/compatibility/part.json"
    write(repo, part, json.dumps(dict(schema_version=1, compatibility=[{"legacy": "old"}])))
    data = dict(schema_version=1, compatibility=[], includes=[part])
    write(repo, path, json.dumps(data))
    assert load_compatibility(repo)["compatibility"] == [{"legacy": "old"}]
    data["includes"] *= 2
    write(repo, path, json.dumps(data))
    with pytest.raises(ValueError, match="duplicate"):
        load_compatibility(repo)
    write(repo, path, json.dumps({**data, "includes": ["../outside.json"]}))
    with pytest.raises(ValueError):
        load_compatibility(repo)
