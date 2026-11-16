"""Opt-in real sandbox evidence for incomplete control, cleanup and durable tails."""

from pathlib import Path
import threading

import pytest

from bash_channel_fixtures import command
from exec_live_fixtures import managed_configuration
from session_terminal_fixtures import CONTEXT
from test_bash_boundary_loss import LOSS_CASES
from test_managed_bash_backend_live import managed_backend, attempt, collect, output_records
from sico.process_identity import boot_id
from sico.service.terminal_dispatcher import TerminalSessionDispatcher
from sico.storage.journal import SessionJournal


@pytest.mark.parametrize('name,text', LOSS_CASES, ids=[row[0] for row in LOSS_CASES])
def test_lost_control_fences_queue_persists_tail_and_confirms_cleanup(managed_backend, tmp_path, name, text):
    backend, claim = managed_backend('workspace-write')
    first = command('(sleep 2; printf escaped > escaped) & ' + text)
    second = command('printf forbidden > next-ran', 'second')
    with SessionJournal(tmp_path, 'session') as journal:
        owner = TerminalSessionDispatcher(journal, CONTEXT, claim, backend=backend,
                                          host_id='boundary-fixture', boot_id=boot_id())
        try:
            owner.submit(first)
            owner.submit(second)
            state = owner.wait(first.ref, 8)
            assert state.phase == 'unknown' and not state.output_complete
            assert not owner.wait(second.ref, 3).succeeded
        finally:
            owner.close()
        root = journal.root
    assert output_records(root, [first])['first']['stdout'] == b'tail'
    assert backend._retirement is not None
    assert not Path('/proc', str(backend._generation.pid)).exists()
    threading.Event().wait(2.1)
    assert not (Path(claim.cwd) / 'escaped').exists()
    assert not (Path(claim.cwd) / 'next-ran').exists()
    reopened, _ = managed_backend()
    with SessionJournal(tmp_path, 'session') as journal:
        owner = TerminalSessionDispatcher(journal, CONTEXT, claim, backend=reopened,
                                          host_id='boundary-fixture', boot_id=boot_id())
        assert owner.wait(first.ref, 0) == state
        assert owner.submit(first) == first.ref
        owner.close()
    assert reopened._attempt is None


@pytest.mark.parametrize('value,expected', [('unset PWD', b'unset'),
                                         ('PWD=relative', b'relative'), ('PWD=', b'')])
def test_host_directory_stays_authoritative_when_user_changes_pwd(managed_backend, value, expected):
    backend, claim = managed_backend()
    creation = attempt(claim)
    generation = backend.start(creation, claim)
    result, streams, contexts = collect(backend, command(value + '; set -u; return 7'))
    assert result.exit_code == 7 and result.shell_ready
    assert contexts[0]['cwd'] == claim.cwd
    result, streams, contexts = collect(backend, command('printf "%s:%s" "$?" "${PWD-unset}"', 'second'))
    assert streams['stdout'] == b'7:' + expected
    assert result.output_complete and result.shell_ready
    assert contexts[0] == dict(cwd=claim.cwd, generation=generation.record())
    backend.close(creation)


def test_long_silent_command_retains_generation_and_trap_state(managed_backend):
    backend, claim = managed_backend()
    creation = attempt(claim)
    generation = backend.start(creation, claim)
    collect(backend, command("shopt -s expand_aliases; alias builtin='printf alias;'; "
                             "trap 'printf returned;' RETURN"))
    result, streams, contexts = collect(backend, command(
        'exec >/dev/null 2>&1; sleep 1.2; return 7', 'second'))
    assert result.exit_code == 7 and result.output_complete and result.shell_ready
    assert streams == dict(stdout=b'', stderr=b'')
    result, streams, contexts = collect(backend, command('printf "%s" "$?"; builtin :', 'third'))
    assert streams['stdout'] == b'7aliasreturned'
    assert contexts[0]['generation'] == generation.record()
    backend.close(creation)
