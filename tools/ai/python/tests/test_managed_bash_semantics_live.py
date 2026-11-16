"""Opt-in same-process source semantics under the pinned managed sandbox."""

import pytest

from bash_channel_fixtures import command
from bash_semantics_cases import CASES, reference_bash
from exec_live_fixtures import managed_configuration
from test_managed_bash_backend_live import managed_backend, attempt, collect


@pytest.mark.parametrize('name,texts,expected', CASES, ids=[row[0] for row in CASES])
def test_managed_state_matches_direct_source(managed_backend, tmp_path, name, texts, expected):
    reference = reference_bash(tmp_path / 'reference', texts)
    assert reference.returncode == 0 and not reference.stderr and reference.stdout == expected
    backend, claim = managed_backend()
    creation = attempt(claim)
    generation = backend.start(creation, claim)
    output = bytearray()
    for index, text in enumerate(texts):
        result, streams, contexts = collect(backend, command(text, str(index)))
        assert result.output_complete and result.shell_ready
        assert contexts[0]['generation'] == generation.record()
        assert not streams['stderr']
        output.extend(streams['stdout'])
    assert output == expected
    backend.close(creation)


def test_managed_status_survives_explicit_cwd_and_does_not_suppress_errexit(managed_backend):
    backend, claim = managed_backend()
    creation = attempt(claim)
    backend.start(creation, claim)
    result, _, _ = collect(backend, command('return 37'))
    assert result.exit_code == 37
    _, streams, _ = collect(backend, command('printf "%s" "$?"', 'second', cwd=claim.cwd + '/next'))
    assert streams['stdout'] == b'37'
    collect(backend, command('set -e', 'third'))
    captured = bytearray()
    with pytest.raises(RuntimeError, match='boundary|ended'):
        backend.execute(command('printf tail; false; printf forbidden', 'fourth'),
            started=lambda *args: None, output=lambda _, data: captured.extend(data), cancelled=lambda: False)
    assert captured == b'tail'
    backend.close(creation)
