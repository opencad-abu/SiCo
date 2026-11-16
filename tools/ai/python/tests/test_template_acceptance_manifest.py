"""Acceptance cannot hide leakage, missing observations or pretend synthetic labels are expert."""

import copy

import pytest
from template_acceptance_manifest import acceptance_report, manifest_for, validate_manifest
from template_search_corpus import cases


@pytest.mark.parametrize("fault", ["family", "source", "duplicate", "signoff"])
def test_reject_leakage_and_unsigned_expert_claim(fault):
    manifest = manifest_for(cases())
    held = next(c for c in manifest["cases"] if c["split"] == "held_out")
    if fault in {"family", "source"}:
        key = "family" if fault == "family" else "source_digest"
        held[key] = manifest["cases"][0][key]
    elif fault == "duplicate":
        manifest["cases"].append(copy.deepcopy(held))
    else:
        manifest["review"] = dict(status="signed", reviewer="test", evidence_ref="private:review",
                                  scope="structure", independent_of_development=True)
    with pytest.raises(ValueError):
        validate_manifest(manifest)


def test_missing_and_wrong_observations_never_close_acceptance():
    manifest = manifest_for(cases())
    empty = acceptance_report(manifest, [])
    assert not empty["machine_passed"] and empty["human_expert_pending"]
    assert empty["held_out"]["pending"] == 36
    c = next(c for c in manifest["cases"] if c["split"] == "held_out")
    report = acceptance_report(manifest, [dict(id=c["id"], input_digest="0" * 64,
        status=c["expected_status"], mapping_matches=True, no_new_recall_loss=True)])
    assert report["failed"] == 1 and not report["machine_passed"]
