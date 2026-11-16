"""Public qualification preflight must never imply hidden execution authority."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from aivworkbench.qualification.dynamic_admission import (
    assess_public_prerequisites, inspect_reservation, inspect_public_candidate,
)
from aivworkbench.workspace import stable_digest


def records():
    candidate = {"reference": {"run_id": "20260908T004841.240166Z-recipe-run-candidate"},
        "policy_status": "approved", "policy_sha256": "a"*64, "plan_file_sha256": "b"*64,
        "correlation_status": "PASS", "failed_public_cases": []}
    reservation = {"reference": {"run_id": "20260908T002653.134041Z-reservation"},
        "domain": {"experiment_sha256": "b"*64, "scope": "single_event_multirate_slice_only"},
        "commitment": {"policy_digest": "a"*64}}
    sequence = deepcopy(reservation)
    sequence["domain"]["scope"] = "sequential_multirate_slice_only"
    return candidate, {"single_event": reservation, "sequence": sequence}


def test_public_prerequisites_never_authorize_private_read_or_qualification():
    result = assess_public_prerequisites(*records())
    assert result["status"] == "PUBLIC_PREREQUISITES_MET"
    assert result["private_access_authorized"] is False
    assert result["candidate_freeze"] == "NOT_PERFORMED"
    assert result["holdout_evaluated"] is False
    assert result["qualification"] == "NOT_ESTABLISHED"


def test_failed_candidate_keeps_analog_and_reports_all_blockers():
    candidate, reservations = records()
    candidate.update(policy_status="proposed", correlation_status="BLOCKED_CONTRACT",
                     failed_public_cases=["startup"])
    result = assess_public_prerequisites(candidate, reservations)
    assert result["status"] == "BLOCKED_HOLDOUT_ADMISSION"
    assert result["disposition"] == "ANALOG_ISLAND"
    assert {b["code"] for b in result["blockers"]} == {
        "ACCEPTANCE_POLICY_NOT_APPROVED", "PUBLIC_CORRELATION_FAILED", "PUBLIC_CORRELATION_NOT_ACCEPTED"}


@pytest.mark.parametrize("field,value,code", [
    ("scope", "single_event_multirate_slice_only", "RESERVATION_SCOPE_MISMATCH"),
    ("experiment_sha256", "c"*64, "RESERVATION_EXPERIMENT_MISMATCH"),
    ("policy_digest", "c"*64, "RESERVATION_POLICY_MISMATCH"),
    ("run_id", "20260908T005000.000000Z-reservation", "RESERVATION_NOT_BEFORE_CANDIDATE_RUN"),
])
def test_reservation_cannot_be_rebound_silently(field, value, code):
    candidate, reservations = records()
    location = "domain" if field in ("scope", "experiment_sha256") else "reference" if field == "run_id" else "commitment"
    reservations["sequence"][location][field] = value
    result = assess_public_prerequisites(candidate, reservations)
    assert result["status"] == "BLOCKED_HOLDOUT_ADMISSION"
    assert result["blockers"] == [{"code": code, "detail": "sequence"}]


def test_missing_sequence_is_not_sufficient_coverage():
    candidate, reservations = records()
    reservations.pop("sequence")
    with pytest.raises(ValueError, match="both required"):
        assess_public_prerequisites(candidate, reservations)


def test_public_commitment_reader_requests_no_private_artifact(monkeypatch):
    domain = {"scope": "single_event_multirate_slice_only", "experiment_sha256": "b"*64}
    commitment = {"schema_version": 1, "locked": True, "public_values_included": False,
        "case_count": 20, "commitment_sha256": "c"*64, "domain_digest": stable_digest(domain),
        "policy_digest": "a"*64, "state": "reserved_before_fit"}
    summary = {"kind": "dynamic-holdout-reservation", "status": "RESERVED_NOT_EVALUATED",
               "commitment": commitment, "domain": domain, "policy_sha256": "a"*64}
    monkeypatch.setattr('aivworkbench.qualification.dynamic_admission._run',
                        lambda control: {"run_status": "RESERVED_NOT_EVALUATED"})
    accesses = []
    def read(control, relative):
        accesses.append(relative)
        assert relative == "public-commitment.json"
        return summary
    monkeypatch.setattr('aivworkbench.qualification.dynamic_admission._json', read)
    assert inspect_reservation('unused')["commitment"]["case_count"] == 20
    assert accesses == ['public-commitment.json']
    commitment['cases'] = ['forbidden-private-vector']
    with pytest.raises(ValueError, match="invalid public"):
        inspect_reservation('unused')


def test_policy_change_rejected_before_waveform_recomputation(tmp_path):
    root = Path(__file__).resolve().parents[3]
    control = root/'.aivw/runs/20260908T004841.240166Z-recipe-run-73b6205911d0/manifest.json'
    if not control.is_file():
        pytest.skip('site-specific source evidence unavailable')
    policy = json.loads((root/'tools/aivw/recipes/ldo/dynamic_acceptance_proposal_v1.json').read_text())
    policy['status'] = 'approved'
    changed = tmp_path/'policy.json'
    changed.write_text(json.dumps(policy))
    with pytest.raises(ValueError, match='selected policy differs'):
        inspect_public_candidate(control, changed, root/'tools/aivw/recipes/ldo/experiment/ldo_master_dynamic_v2.json')


def test_stored_pass_label_cannot_replace_recomputed_correlation(monkeypatch):
    from aivworkbench.qualification import dynamic_admission as module
    root = Path(__file__).resolve().parents[3]
    control = root/'.aivw/runs/20260908T004841.240166Z-recipe-run-73b6205911d0/manifest.json'
    if not control.is_file():
        pytest.skip('site-specific source evidence unavailable')
    original = module._json
    expected = original(control, 'checks/gates/correlation/correlation.json')
    def altered(control, relative):
        value = original(control, relative)
        if relative.endswith('/correlation.json'):
            value['status'] = 'PASS'
            value['diagnostic_status'] = 'PASS'
        return value
    monkeypatch.setattr(module, '_json', altered)
    # The actual end-to-end audits exercise the numeric comparator. Here isolate
    # the admission boundary using the already authenticated failed comparison.
    monkeypatch.setattr(module, 'correlation_report', lambda *a, **k: expected)
    with pytest.raises(ValueError, match='independently recomputed'):
        inspect_public_candidate(control, root/'tools/aivw/recipes/ldo/dynamic_acceptance_proposal_v1.json',
                                 root/'tools/aivw/recipes/ldo/experiment/ldo_master_dynamic_v2.json')


def test_real_reservation_read_never_opens_private_paths(monkeypatch):
    import io
    root = Path(__file__).resolve().parents[3]
    control = root/'.aivw/runs/20260908T002653.134041Z-dynamic-holdout-reservation-18adc08f1ac6/manifest.json'
    if not control.is_file():
        pytest.skip('site-specific reservation unavailable')
    old = io.open
    def guarded(file, *args, **kwargs):
        if isinstance(file, (str, Path)):
            assert 'private' not in Path(file).parts
            assert Path(file).name != 'reservation.json'
        return old(file, *args, **kwargs)
    monkeypatch.setattr(io, 'open', guarded)
    assert inspect_reservation(control)['commitment']['case_count'] == 8


def test_original_plan_bytes_bind_reservation_despite_gate_normalization(monkeypatch):
    from aivworkbench.qualification import dynamic_admission as module
    from aivworkbench.workspace import sha256_file
    root = Path(__file__).resolve().parents[3]
    control = root/'.aivw/runs/20260908T004841.240166Z-recipe-run-73b6205911d0/manifest.json'
    if not control.is_file():
        pytest.skip('site-specific source evidence unavailable')
    old = module._json(control, 'checks/gates/correlation/correlation.json')
    monkeypatch.setattr(module, 'correlation_report', lambda *a, **k: old)
    plan = root/'tools/aivw/recipes/ldo/experiment/ldo_master_dynamic_v2.json'
    policy = root/'tools/aivw/recipes/ldo/dynamic_acceptance_proposal_v1.json'
    candidate = inspect_public_candidate(control, policy, plan)
    assert candidate['plan_file_sha256'] == sha256_file(plan)
    # The authenticated gate serializes with different formatting, not a new experiment.
    assert candidate['plan_artifact_sha256'] != candidate['plan_file_sha256']
    reservation = inspect_reservation(root/'.aivw/runs/20260908T002653.134041Z-dynamic-holdout-reservation-18adc08f1ac6/manifest.json')
    assert reservation['domain']['experiment_sha256'] == candidate['plan_file_sha256']
    assert reservation['commitment']['policy_digest'] == candidate['policy_sha256']


FROZEN_RUNS = {
    'candidate': '20261011T075330.234919Z-refined-matrix-294ea9693bf0',
    'reference': '20260908T080129.259791Z-recipe-run-4178cd65898d',
    'crosscheck': '20260908T080212.970816Z-recipe-run-fd54d4ee35e5',
    'source': '20260908T090908.052018Z-transition-response-3007d9977a29',
    'single_event': '20261011T074029.321759Z-dynamic-holdout-reservation-df80ec2e30c2',
    'sequence': '20261011T074029.502723Z-dynamic-holdout-reservation-b0dea7238454',
}


def _frozen_controls():
    root = Path(__file__).resolve().parents[3]
    controls = {key: root/'.aivw/runs'/run_id/'manifest.json' for key, run_id in FROZEN_RUNS.items()}
    if not all(path.is_file() for path in controls.values()):
        pytest.skip('v2 frozen-candidate evidence unavailable')
    return root, controls


def test_frozen_candidate_reaches_public_prerequisites_under_v2():
    from aivworkbench.qualification.dynamic_admission import audit_frozen_admission
    root, controls = _frozen_controls()
    summary = audit_frozen_admission(
        controls['candidate'], controls['reference'], controls['crosscheck'], controls['source'],
        controls['single_event'], controls['sequence'],
        root/'tools/aivw/recipes/ldo/dynamic_acceptance_v2.json',
        root/'tools/aivw/recipes/ldo/experiment/ldo_master_dynamic_v2.json')
    assert summary['candidate']['correlation_status'] == 'PASS'
    assert summary['candidate']['policy_status'] == 'approved'
    assert summary['blockers'] == []
    assert summary['status'] == 'PUBLIC_PREREQUISITES_MET'
    assert summary['candidate_freeze'] == 'NOT_PERFORMED'


def test_frozen_recomputation_rejects_altered_stored_correlation(monkeypatch):
    from aivworkbench.qualification import dynamic_admission as module
    root, controls = _frozen_controls()
    original = module._artifact

    def altered(control, relative):
        path = original(control, relative)
        if Path(control) == controls['candidate'] and relative == 'correlation.json':
            value = json.loads(path.read_text())
            value['status'] = 'PASS'
            value['cases'][0]['diagnostic_status'] = 'FAIL_TOLERANCE'
            changed = path.parent/'altered-correlation.json'
            changed.write_text(json.dumps(value))
            return changed
        return path

    monkeypatch.setattr(module, '_artifact', altered)
    with pytest.raises(ValueError, match='independently recomputed'):
        module.inspect_frozen_candidate(
            controls['candidate'], root/'tools/aivw/recipes/ldo/dynamic_acceptance_v2.json',
            root/'tools/aivw/recipes/ldo/experiment/ldo_master_dynamic_v2.json',
            reference_control=controls['reference'], crosscheck_control=controls['crosscheck'],
            source_control=controls['source'])


ADMISSION_RUN = '20261011T083401.691091Z-dynamic-admission-audit-67d257c12985'


def test_candidate_freeze_binds_the_admitted_candidate(tmp_path):
    from aivworkbench.qualification import dynamic_admission as module
    from aivworkbench.qualification.candidate_freeze import freeze_candidate, inspect_freeze
    root, controls = _frozen_controls()
    admission = root/'.aivw/runs'/ADMISSION_RUN/'manifest.json'
    if not admission.is_file():
        pytest.skip('v2 admission evidence unavailable')
    summary = freeze_candidate(
        controls['candidate'], controls['reference'], controls['crosscheck'], controls['source'],
        controls['single_event'], controls['sequence'], admission,
        root/'tools/aivw/recipes/ldo/dynamic_acceptance_v2.json',
        root/'tools/aivw/recipes/ldo/experiment/ldo_master_dynamic_v2.json')
    assert summary['integrity'] == 'PASS'
    freeze_control = Path(summary['control_manifest'])
    admitted = module._json(admission, 'summary.json')['candidate']
    record = inspect_freeze(freeze_control, admitted)
    assert record['candidate_sha256'] == admitted['candidate_sha256']
    assert record['sources']['matrix']['run_id'] == admitted['reference']['run_id']
    altered = dict(admitted)
    altered['candidate_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='another candidate decision'):
        inspect_freeze(freeze_control, altered)
