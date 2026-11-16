"""Real Bash must fence lost control without timing out ordinary long commands."""

import os
import shlex
import sys
import time

import pytest

from bash_channel_fixtures import bash_session, command
from sico.terminal.command_pipes import CommandPipes


LOSS_CASES = [
    ('noexec', 'printf tail; set -n; printf forbidden'),
    ('disabled-printf', 'printf tail; enable -n printf'),
    ('replaced-builtin', 'printf tail; builtin(){ :; }'),
    ('skipped-debug', "printf tail; shopt -s extdebug; trap 'false' DEBUG"),
]


@pytest.mark.parametrize('name,text', LOSS_CASES, ids=[row[0] for row in LOSS_CASES])
def test_lost_completion_is_detected_from_scope_end_and_tail_is_preserved(bash_session, name, text):
    captured = bytearray()
    with bash_session() as shell:
        started = time.monotonic()
        with pytest.raises(ValueError, match='before command completion'):
            shell.run(command(text), on_output=lambda stream, data:
                      captured.extend(data) if stream == 'stdout' else None)
        assert time.monotonic() - started < 3
        assert captured == b'tail'
        with pytest.raises(ValueError):
            shell.submit(command('printf forbidden', 'second'))


def test_internal_builtin_tokens_are_not_expanded_as_user_aliases(bash_session):
    with bash_session() as shell:
        shell.run(command("shopt -s expand_aliases; alias builtin='printf alias;'; return 7"))
        facts, streams = shell.run(command('printf "%s" "$?"; builtin :', 'second'))
        assert facts[-1].value == 0
        assert streams == dict(stdout=b'7alias', stderr=b'')


@pytest.mark.parametrize('value,expected', [('unset PWD', b'unset'),
                                         ('PWD=relative', b'relative'), ('PWD=', b'')])
def test_user_pwd_variable_is_not_required_for_control(bash_session, value, expected):
    with bash_session() as shell:
        facts, streams = shell.run(command(value + '; set -u; printf "%s" "${PWD-unset}"'))
        assert facts[-1].kind == 'finished' and streams['stdout'] == expected
        _, streams = shell.run(command('printf "%s" "${PWD-unset}"', 'second'))
        assert streams['stdout'] == expected


def test_scope_pipe_is_closed_for_user_code_and_external_children(bash_session):
    with bash_session() as shell:
        pipes = shell.submit(command('sleep .05'))
        # This method is an observation, never evidence of successful execution.
        assert pipes.scope_ended() is False
    script = "import os; print(sorted(int(x) for x in os.listdir('/proc/self/fd') if int(x)>3))"
    with bash_session() as shell:
        _, streams = shell.run(command(shlex.join([sys.executable, '-B', '-s', '-c', script])))
        assert streams['stdout'] == b'[]\n'


def test_user_closing_output_does_not_mean_the_command_scope_has_ended(bash_session):
    with bash_session() as shell:
        start = time.monotonic()
        facts, streams = shell.run(command('exec >/dev/null 2>&1; sleep .15; return 7'))
        assert time.monotonic() - start >= .14
        assert facts[-1].value == 7 and streams == dict(stdout=b'', stderr=b'')


def test_background_tail_does_not_keep_the_scope_pipe_open(bash_session):
    with bash_session() as shell:
        pipes = shell.submit(command('(sleep .15; printf late) & printf now'))
        deadline = time.monotonic() + 2
        finished = False
        while time.monotonic() < deadline and not finished:
            try:
                data = os.read(shell.child.stdout.fileno(), 65536)
            except BlockingIOError:
                time.sleep(.002)
                continue
            for fact in shell.control.feed(data):
                pipes.observe_open()
                if fact.kind == 'started':
                    pid = fact.value
                if fact.kind == 'finished':
                    finished = True
                    pipes.release_writers()
        assert finished
        deadline = time.monotonic() + .1
        while not pipes.scope_ended(pid) and time.monotonic() < deadline:
            time.sleep(.002)
        assert pipes.scope_ended(pid)
        # Output ownership is independent: the background child still holds it.
        assert pipes.read('stdout') == b'now'
        assert pipes.read('stdout') is None


def test_lost_boundary_does_not_wait_for_background_writers(bash_session):
    captured = bytearray()
    with bash_session() as shell:
        started = time.monotonic()
        with pytest.raises(ValueError, match='before command completion'):
            shell.run(command('(sleep 30; printf too-late) & printf tail; set -n'),
                on_output=lambda stream, data: captured.extend(data) if stream == 'stdout' else None)
        assert time.monotonic() - started < 3
        assert captured == b'tail'


def test_scope_observation_failure_never_claims_release(monkeypatch):
    import sico.terminal.command_pipes as module
    pipes = CommandPipes(command())
    writer = os.dup(pipes._scope_writer)
    pipes.observe_open()
    try:
        monkeypatch.setattr(module.os, 'listdir', lambda path: ['4'])
        readlink = module.os.readlink
        def gone(path):
            if path.startswith('/proc/123/'):
                raise FileNotFoundError(path)
            return readlink(path)
        monkeypatch.setattr(module.os, 'readlink', gone)
        assert not pipes.scope_ended(123)
        monkeypatch.setattr(module.os, 'listdir', lambda path: list(map(str, range(4097))))
        with pytest.raises(ValueError, match='budget'):
            pipes.scope_ended(123)
    finally:
        os.close(writer)
        pipes.close()
