"""Malformed records and stale references cannot qualify as compatibility evidence."""

import copy
from pathlib import Path

import pytest

from utility.a_philosophy_compat import check_compatibility, load_compatibility
from conftest import write


@pytest.fixture
def compatibility(repo):
    write(repo, "api.py", "class Owner:\n    def execute(self):\n        pass\n")
    row = dict(
        legacy="legacy.run",
        source="api.py",
        replacement="api.py:Owner.execute",
        consumers=["api.py"],
        owner="api.py",
        evidence=["api.py"],
        status="retained",
        allow_exposure=True,
        exit_condition="Remove after caller migration.",
    )
    return repo, dict(schema_version=1, compatibility=[row])


def test_reviewed_repository_records_have_existing_references():
    root = Path(__file__).resolve().parents[3]
    payload = load_compatibility(root)
    assert check_compatibility(root, payload) == []


@pytest.mark.parametrize(
    "key,value,fragment",
    [
        ("exit_condition", "", "exit_condition must be non-empty"),
        ("consumers", [""], "consumers must contain"),
        ("evidence", [], "evidence must contain"),
        ("allow_exposure", "yes", "allow_exposure must be boolean"),
        ("owner", "missing.py", "missing/invalid compatibility reference"),
        ("replacement", "api.py:Owner.absent", "missing compatibility definition"),
        ("status", "permanent", "invalid compatibility status"),
    ],
)
def test_invalid_record_is_rejected(compatibility, key, value, fragment):
    root, data = compatibility
    data["compatibility"][0][key] = value
    assert any(fragment in e for e in check_compatibility(root, data))


def test_duplicate_legacy_rejected(compatibility):
    root, data = compatibility
    data["compatibility"].append(copy.deepcopy(data["compatibility"][0]))
    assert any("duplicate legacy entry" in e for e in check_compatibility(root, data))


def test_tombstone_cannot_claim_exposure_is_allowed(compatibility):
    root, data = compatibility
    data["compatibility"][0]["status"] = "tombstone"
    assert any("retired operation cannot" in e for e in check_compatibility(root, data))


def test_non_object_legacy_value_reports_schema_error(compatibility):
    root, data = compatibility
    data["compatibility"][0]["legacy"] = []
    assert any("legacy must be non-empty" in e for e in check_compatibility(root, data))


def test_no_repository_consumer_requires_an_explicit_external_scope(compatibility):
    root, data = compatibility
    data["compatibility"][0]["consumers"] = []
    assert any("consumers must contain" in e for e in check_compatibility(root, data))
    data["compatibility"][0]["external_consumers"] = "Supported site imports; none found in repository."
    assert check_compatibility(root, data) == []


@pytest.mark.parametrize("status", ["retained", "tombstone", "removed"])
def test_only_removed_source_may_be_absent(compatibility, status):
    root, data = compatibility
    data["compatibility"][0].update(source="retired.py", status=status, allow_exposure=False)
    errors = check_compatibility(root, data)
    assert bool(errors) == (status != "removed")


@pytest.mark.parametrize("field", ["owner", "replacement"])
def test_removed_record_still_requires_current_authority(compatibility, field):
    root, data = compatibility
    data["compatibility"][0].update(source="retired.py", status="removed", allow_exposure=False)
    data["compatibility"][0][field] = "retired.py"
    assert check_compatibility(root, data)
