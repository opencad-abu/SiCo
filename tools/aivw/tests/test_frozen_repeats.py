import json

import pytest

from aivworkbench.qualification.frozen_repeats import (
    freeze_inputs, verify_freeze, begin_attempt, finish_attempt, compare_attempts,
)


def frozen(tmp_path):
    source = tmp_path/'candidate.sv'
    source.write_text('module synthetic; endmodule\n')
    root = tmp_path/'journal'
    value = freeze_inputs(root, {'candidate.sv': source}, {'dataset': 'synthetic'})
    return root, value['freeze_digest'], source


def complete(root, digest, number, measurement='a', status='EXECUTED'):
    begin_attempt(root, digest, number, 'synthetic-run-%d' % number)
    return finish_attempt(root, digest, number, status=status, measurements={'waveform': measurement})


def test_two_independent_frozen_repeats_do_not_claim_qualification(tmp_path):
    root, digest, source = frozen(tmp_path)
    source.write_text('changed outside snapshot')
    verify_freeze(root, digest)
    complete(root, digest, 1)
    complete(root, digest, 2)
    result = compare_attempts(root, digest)
    assert result['status'] == 'PASS_REPEAT'
    assert result['qualification'] == 'NOT_ESTABLISHED'
    with pytest.raises(FileExistsError):
        freeze_inputs(root, {'candidate.sv': source}, {'dataset': 'synthetic'})


def test_difference_between_repeats_is_failure(tmp_path):
    root, digest, _ = frozen(tmp_path)
    complete(root, digest, 1)
    complete(root, digest, 2, 'changed')
    assert compare_attempts(root, digest)['status'] == 'FAIL_REPEAT'


def test_crash_marker_prevents_retry_and_second_execution(tmp_path):
    root, digest, _ = frozen(tmp_path)
    begin_attempt(root, digest, 1, 'interrupted')
    with pytest.raises(FileExistsError):
        begin_attempt(root, digest, 1, 'retry')
    with pytest.raises(ValueError, match='unknown'):
        begin_attempt(root, digest, 2, 'second')


@pytest.mark.parametrize('status', ['FAILED', 'UNKNOWN_SIDE_EFFECT'])
def test_failed_first_cannot_continue(tmp_path, status):
    root, digest, _ = frozen(tmp_path)
    complete(root, digest, 1, status=status)
    with pytest.raises(ValueError, match='successful independent'):
        begin_attempt(root, digest, 2, 'next')


def test_changed_candidate_detected_before_start_and_completion(tmp_path):
    root, digest, _ = frozen(tmp_path)
    begin_attempt(root, digest, 1, 'run')
    (root/'inputs/candidate.sv').write_text('tampered')
    with pytest.raises(ValueError, match='input changed'):
        finish_attempt(root, digest, 1, status='EXECUTED', measurements={'wave': 'a'})
    with pytest.raises(ValueError, match='input changed'):
        begin_attempt(root, digest, 2, 'next')


def test_record_tampering_and_duplicate_identity_rejected(tmp_path):
    root, digest, _ = frozen(tmp_path)
    complete(root, digest, 1)
    with pytest.raises(ValueError, match='independent'):
        begin_attempt(root, digest, 2, 'synthetic-run-1')
    p = root/'attempt_1/result.json'
    result = json.loads(p.read_text())
    result['measurements']['waveform'] = 'tampered'
    p.write_text(json.dumps(result))
    with pytest.raises(ValueError, match='binding differs'):
        begin_attempt(root, digest, 2, 'synthetic-run-2')


def test_no_third_attempt_or_completion_overwrite(tmp_path):
    root, digest, _ = frozen(tmp_path)
    complete(root, digest, 1)
    with pytest.raises(FileExistsError):
        finish_attempt(root, digest, 1, status='EXECUTED', measurements={'wave': 'different'})
    with pytest.raises(ValueError):
        begin_attempt(root, digest, 3, 'third')
    with pytest.raises(ValueError):
        begin_attempt(root, digest, True, 'boolean')


def test_symlink_and_parent_traversal_rejected(tmp_path):
    root, digest, source = frozen(tmp_path)
    other = tmp_path/'link'
    other.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError, match='non-symlink'):
        verify_freeze(other, digest)
    with pytest.raises(ValueError, match='filename'):
        freeze_inputs(tmp_path/'new', {'../escape': source}, {'dataset': 'synthetic'})
