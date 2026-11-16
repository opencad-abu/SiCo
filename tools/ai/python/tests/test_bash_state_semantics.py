"""Compare persistent command state with an independent top-level Bash source."""

import pytest

from bash_channel_fixtures import bash_session, command
from bash_semantics_cases import CASES, reference_bash
from sico.terminal.bash_control import BashControl
from sico.terminal.command_pipes import CommandPipes


@pytest.mark.parametrize('name,texts,expected', CASES, ids=[row[0] for row in CASES])
def test_persistent_state_matches_direct_source(bash_session, tmp_path, name, texts, expected):
    reference = reference_bash(tmp_path / 'reference', texts)
    assert reference.returncode == 0 and not reference.stderr
    assert reference.stdout == expected
    with bash_session() as shell:
        captured = bytearray()
        for index, text in enumerate(texts):
            facts, streams = shell.run(command(text, str(index)))
            assert [fact.kind for fact in facts] == ['started', 'finished']
            assert not streams['stderr']
            captured.extend(streams['stdout'])
        assert captured == reference.stdout


def test_explicit_directory_change_does_not_reset_previous_result(bash_session, tmp_path):
    target = tmp_path / 'next'; target.mkdir()
    with bash_session() as shell:
        shell.run(command('return 23'))
        facts, streams = shell.run(command('printf "%s" "$?"', 'second', cwd=str(target)))
        assert facts[0].cwd == str(target) and streams['stdout'] == b'23'


def test_rejected_directory_does_not_replace_last_executed_status(bash_session, tmp_path):
    with bash_session() as shell:
        shell.run(command('return 23'))
        facts, _ = shell.run(command('printf forbidden', 'second', cwd=str(tmp_path / 'absent')))
        assert facts[-1].kind == 'rejected'
        _, streams = shell.run(command('printf "%s" "$?"', 'third'))
        assert streams['stdout'] == b'23'


def test_source_syntax_error_is_the_next_command_status(bash_session):
    with bash_session() as shell:
        facts, _ = shell.run(command('if then'))
        assert facts[-1].value == 2
        _, streams = shell.run(command('printf "%s" "$?"', 'second'))
        assert streams['stdout'] == b'2'


@pytest.mark.parametrize('status', [True, -1, 256, '1; printf injected'])
def test_status_restore_rejects_non_exit_code_values(status):
    pipes = CommandPipes(command())
    try:
        with pytest.raises(ValueError, match='previous Bash status'):
            pipes.dispatch(9, previous_status=status)
    finally:
        pipes.close()


def test_incomplete_control_cannot_publish_a_new_exit_status():
    control = BashControl()
    control.expect('first')
    control.feed(b'started\0first\0' + b'123\0/\0finished\0first\0' + b'7\0/')
    assert control.last_status == 0
    with pytest.raises(ValueError):
        control.eof()


@pytest.mark.parametrize('with_cwd', [False, True])
def test_errexit_is_not_disabled_by_status_restoration(bash_session, tmp_path, with_cwd):
    with bash_session() as shell:
        shell.run(command('set -e'))
        with pytest.raises(ValueError, match='before command completion'):
            shell.run(command('false; printf must-not-run', 'second', cwd=str(tmp_path) if with_cwd else None))


def test_command_stream_redirection_is_deliberately_scoped(bash_session):
    with bash_session() as shell:
        _, streams = shell.run(command('exec >/dev/null; printf hidden'))
        assert streams['stdout'] == b''
        _, streams = shell.run(command('printf visible', 'second'))
        assert streams['stdout'] == b'visible'


def test_debug_observer_can_see_integration_but_cannot_forge_output_as_control(bash_session):
    with bash_session() as shell:
        facts, streams = shell.run(command("trap 'printf \"debug\\n\"' DEBUG; printf body"))
        assert [row.kind for row in facts] == ['started', 'finished']
        assert b'body' in streams['stdout'] and streams['stdout'].count(b'debug\n') > 1
        _, streams = shell.run(command('printf next; trap - DEBUG', 'second'))
        assert b'next' in streams['stdout']
