"""Fixed-runtime managed Bash commands, kernel identity and durable session output."""

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shlex
import sys
import threading

import pytest

from bash_channel_fixtures import command
from exec_live_fixtures import managed_configuration
from session_terminal_fixtures import CLAIM, CONTEXT
from sico.core.terminal_output import OutputChunk
from sico.core.terminal_session import TerminalAttempt
from sico.execution.managed_bash import ManagedBashBackend
from sico.process_identity import boot_id
from sico.service.terminal_dispatcher import TerminalSessionDispatcher
from sico.storage.history_reader import SessionReader
from sico.storage.journal import SessionJournal
from sico.storage.terminal_records import JOURNAL_KIND, restore


@pytest.fixture
def managed_backend(managed_configuration):
    binary, control, child, work, home = managed_configuration
    instances = []
    def create(access='read-only'):
        backend = ManagedBashBackend(binary, control, cwd=work, home=home, environment=child,
            environment_id=CLAIM.environment_id, access=dict(sandbox=access, approval='never', network=False))
        instances.append(backend)
        return backend, replace(CLAIM, cwd=str(work))
    yield create
    for backend in instances:
        if backend._attempt is not None:
            backend.close(backend._attempt)


def collect(backend, request, *, cancel=lambda: False):
    streams, contexts = dict(stdout=bytearray(), stderr=bytearray()), []
    result = backend.execute(request, started=lambda execution, context: contexts.append(context),
        output=lambda stream, data: streams[stream].extend(data), cancelled=cancel)
    return result, {key: bytes(value) for key, value in streams.items()}, contexts


def attempt(claim):
    return TerminalAttempt(claim.ref, 1, 'managed-fixture', boot_id())


def output_records(root, requests):
    reader = SessionReader(root, 'session')
    result = {r.ref.command_id: dict(stdout=bytearray(), stderr=bytearray()) for r in requests}
    for row in reader.stream_events():
        if row['kind'] == JOURNAL_KIND:
            fact = restore(row['payload'], reader)
            if fact['kind'] == 'output':
                chunk = OutputChunk.from_record(fact['chunk'])
                result[chunk.ref.command_id][chunk.stream].extend(chunk.data)
    return result


def test_same_managed_shell_persists_large_output_and_reopens_without_execution(managed_backend, tmp_path):
    backend, claim = managed_backend()
    expected = ('中文\0\x1b[31m\n' * 50000).encode() + b'\xff\xfe'
    script = "import os; os.write(1,('中文\\0\\x1b[31m\\n'*50000).encode()+b'\\xff\\xfe'); os.write(2,b'error\\0\\xff')"
    requests = [command('cd next; export SICO_FIXTURE=kept; fixture() { printf function; }; printf first'),
                command(f'{shlex.quote(sys.executable)} -c {shlex.quote(script)}', 'second'),
                command('PWD=/forged; printf "%s:" "$SICO_FIXTURE"; fixture; (sleep .05; printf late) & printf now', 'third')]
    states = []
    with SessionJournal(tmp_path, 'session') as journal:
        owner = TerminalSessionDispatcher(journal, CONTEXT, claim, backend=backend,
                                          host_id='managed-fixture', boot_id=boot_id())
        try:
            for request in requests:
                owner.submit(request)
                state = owner.wait(request.ref, 15)
                assert state.succeeded and state.output_complete, owner.snapshot()
                states.append(state)
            assert len({state.generation for state in states}) == 1
            assert states[0].execution_cwd == claim.cwd
            assert [state.execution_cwd for state in states[1:]] == [str(Path(claim.cwd) / 'next')] * 2
        finally:
            owner.close()
        root = journal.root
    output = output_records(root, requests)
    assert output['first']['stdout'] == b'first'
    assert hashlib.sha256(output['second']['stdout']).digest() == hashlib.sha256(expected).digest()
    assert output['second']['stderr'] == b'error\0\xff'
    assert output['third']['stdout'] == b'kept:functionnowlate'
    reopened, _ = managed_backend()
    with SessionJournal(tmp_path, 'session') as journal:
        owner = TerminalSessionDispatcher(journal, CONTEXT, claim, backend=reopened,
                                          host_id='managed-fixture', boot_id=boot_id())
        for request, state in zip(requests, states):
            assert owner.wait(request.ref, 0) == state
            assert owner.submit(request) == request.ref
        owner.close()
    assert reopened._attempt is None


@pytest.mark.parametrize('access,writable', [('read-only', False), ('workspace-write', True)])
def test_combined_shell_retains_sandbox_scope_and_explicit_environment(managed_backend, tmp_path, access, writable):
    backend, claim = managed_backend(access)
    creation = attempt(claim)
    backend.start(creation, claim)
    script = """
import json, os, pathlib, socket, sys
result = {}
for name, path in [('inside', pathlib.Path('allowed')), ('outside', pathlib.Path(sys.argv[1]))]:
    try: path.write_text('fixture'); result[name] = True
    except OSError: result[name] = False
try: socket.socket().bind(('127.0.0.1', 0)); result['network'] = True
except OSError: result['network'] = False
result['secret'] = 'SICO_API_KEY' in os.environ
result['helper_path'] = 'PYTHONPATH' in os.environ
print(json.dumps(result))
"""
    request = command(shlex.join([sys.executable, '-c', script, str(tmp_path / 'outside')]))
    result, streams, contexts = collect(backend, request)
    assert result.output_complete and result.shell_ready
    assert json.loads(streams['stdout']) == dict(inside=writable, outside=False, network=False,
                                                 secret=False, helper_path=False)
    assert contexts[0]['cwd'] == claim.cwd
    backend.close(creation)


@pytest.mark.parametrize('text', ['printf tail; printf err >&2; exit 9',
                                  'printf tail; exec /bin/true', 'printf tail; set -e; false'])
def test_shell_loss_retains_tail_without_fabricating_completion(managed_backend, tmp_path, text):
    backend, claim = managed_backend()
    request = command(text)
    with SessionJournal(tmp_path, 'session') as journal:
        owner = TerminalSessionDispatcher(journal, CONTEXT, claim, backend=backend,
                                          host_id='managed-fixture', boot_id=boot_id())
        try:
            owner.submit(request)
            state = owner.wait(request.ref, 8)
            assert state.phase == 'unknown' and not state.output_complete
        finally:
            owner.close()
        root = journal.root
    assert output_records(root, [request])['first']['stdout'] == b'tail'


def test_cancel_stops_generation_and_reaps_delayed_background_effect(managed_backend):
    backend, claim = managed_backend('workspace-write')
    creation = attempt(claim)
    generation = backend.start(creation, claim)
    cancel = threading.Event()
    request = command('(sleep 1; printf escaped > escaped) & printf ready; sleep 30')
    seen = bytearray()
    def output(stream, data):
        seen.extend(data)
        if b'ready' in seen: cancel.set()
    with pytest.raises(RuntimeError):
        backend.execute(request, started=lambda *args: None, output=output, cancelled=cancel.is_set)
    evidence = backend.close(creation)
    assert evidence.generation == generation and evidence == backend.close(creation)
    threading.Event().wait(1.2)
    assert not (Path(claim.cwd) / 'escaped').exists()
    with pytest.raises(RuntimeError):
        backend.start(creation, claim)


def test_maximum_command_crosses_transport_blocks_without_reexecution(managed_backend):
    backend, claim = managed_backend()
    creation = attempt(claim)
    backend.start(creation, claim)
    prefix = 'printf once; #'
    request = command(prefix + 'x' * (65536 - len(prefix)))
    result, streams, _ = collect(backend, request)
    assert result.output_complete and streams['stdout'] == b'once'
    backend.close(creation)
