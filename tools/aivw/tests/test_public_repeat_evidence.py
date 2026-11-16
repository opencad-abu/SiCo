from pathlib import Path

import pytest

from aivworkbench.qualification.public_repeat_evidence import read_public_repeat_certificate


@pytest.mark.parametrize('run_id,count', [
    ('20260908T015109.895794Z-public-frozen-repeats-2461c182a800', 17),
    ('20260908T015116.577837Z-public-frozen-repeats-37857f4d5a26', 24),
])
def test_authenticates_frozen_journal_children_and_original_csv(run_id, count):
    control = Path(__file__).resolve().parents[3]/'.aivw/runs'/run_id/'manifest.json'
    if not control.is_file():
        pytest.skip('site-specific public repeat evidence unavailable')
    result = read_public_repeat_certificate(control)
    assert result['status'] == 'PASS_PUBLIC_REPEAT_EVIDENCE'
    assert result['case_count'] == count and result['repeat_count'] == 2
    assert not result['holdout_evaluated']
    assert result['qualification'] == 'NOT_ESTABLISHED'


def test_inconsistent_journal_cannot_be_overridden_by_report_pass(monkeypatch):
    control = Path(__file__).resolve().parents[3]/'.aivw/runs/20260908T015109.895794Z-public-frozen-repeats-2461c182a800/manifest.json'
    if not control.is_file():
        pytest.skip('site-specific public repeat evidence unavailable')
    monkeypatch.setattr('aivworkbench.qualification.public_repeat_evidence.compare_attempts',
                        lambda *args: {'status': 'FAIL_REPEAT'})
    with pytest.raises(ValueError, match='comparison differs'):
        read_public_repeat_certificate(control)
