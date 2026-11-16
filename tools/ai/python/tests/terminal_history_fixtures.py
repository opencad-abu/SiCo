"""Durable terminal history fixtures with no execution backend."""

from sico.core.contracts import RunState
from sico.core.terminal_output import OutputChunk
from sico.core.terminal_state import event
from sico.storage.history_reader import SessionReader
from sico.storage.terminal_history import TerminalHistory
from sico.storage.terminal_records import JOURNAL_KIND, store
from session_terminal_fixtures import CONTEXT, request


def append(journal, observation):
    return journal.append(JOURNAL_KIND, store(observation, journal), RunState(CONTEXT))


def recorded(journal, name='first', chunks=(), *, finish=True, code=0, **fields):
    command = request(name, **fields)
    append(journal, event('accepted', command.ref, request=command.record()))
    append(journal, event('started', command.ref, execution_id='execution-' + name))
    offset, sequence, artifacts = 0, 0, []
    for stream, data in chunks:
        sequence += 1
        chunk = OutputChunk(command.ref, sequence, offset, stream, data)
        row = append(journal, event('output', command.ref, chunk=chunk.record()))
        artifacts.append(row['payload']['chunk']['artifact'])
        offset = chunk.end
    if finish:
        append(journal, event('exited', command.ref, exit_code=code, signal=None,
                              output_complete=True, output_sequence=sequence, output_bytes=offset))
    return command, artifacts


def history(root):
    reader = SessionReader(root, 'session')
    return TerminalHistory(reader), reader
