"""Read terminal metadata and selected output from one existing journal snapshot.

No writer, derived state file, process owner or execution capability is created.
Metadata scans are linear in journal length; retained command and output data
are bounded. Each call opens a fresh snapshot, so polling can discover appends.
"""

from contextlib import contextmanager

from ..core.terminal_commands import CommandRef
from ..core.terminal_output import OutputChunk, position
from ..core import terminal_receipts as views
from ..core.terminal_state import advance_output, apply_event, recovered
from .terminal_records import JOURNAL_CONTRACT, JOURNAL_KIND, restore, validate_metadata


class _Snapshot:
    def __init__(self, reader, events):
        self.reader, self._events = reader, events
        self.sequence = 0

    def events(self):
        self.sequence = 0
        for row in self._events():
            self.sequence = row['sequence']
            if row['kind'] != JOURNAL_KIND:
                continue
            value = row['payload']
            ref = validate_metadata(value, self.reader.session_id)
            if value['contract'] == JOURNAL_CONTRACT:
                yield row['sequence'], ref, value, row.get('timestamp')

    def apply(self, state, value):
        if value['kind'] != 'output':
            return apply_event(state, restore(value, self.reader))
        chunk = value['chunk']
        return advance_output(state, CommandRef.from_record(value['ref']), chunk['sequence'],
                              chunk['offset'], chunk['stream'], chunk['size'])


class TerminalHistory:
    """Read-only authority for terminal lists, status, pages and bounded receipts."""

    def __init__(self, reader):
        if not callable(getattr(reader, 'snapshot_events', None)):
            raise TypeError('Terminal history requires read-only snapshot capability')
        self._reader = reader

    @contextmanager
    def _snapshot(self):
        with self._reader.snapshot_events() as events:
            yield _Snapshot(self._reader, events)

    def _ref(self, ref):
        if not isinstance(ref, CommandRef) or ref.session_id != self._reader.session_id:
            raise ValueError('Command belongs to another session')

    def commands(self, *, after=0, limit=views.MAX_COMMANDS):
        """Page admissions in capture order; next_after is an opaque integer cursor.

        Exact repeated admissions do not create a second command. They consume
        cursor space, so a page may be short or empty while still having a next.
        """
        position(after)
        views.limit(limit, views.MAX_COMMANDS)
        with self._snapshot() as snapshot:
            selected, count, cursor, more = {}, 0, after, False
            for sequence, ref, value, _timestamp in snapshot.events():
                if value['kind'] != 'accepted' or sequence <= after:
                    continue
                if count == limit:
                    more = True
                    break
                selected.setdefault(ref, None)
                count, cursor = count + 1, sequence
            first = {}
            for sequence, ref, value, _timestamp in snapshot.events():
                if ref in selected:
                    if value['kind'] == 'accepted':
                        first.setdefault(ref, sequence)
                    selected[ref] = snapshot.apply(selected[ref], value)
            if after > snapshot.sequence:
                raise ValueError('Terminal history cursor exceeds the snapshot')
            commands = [views.summary(recovered(state)) for ref, state in selected.items()
                        if first[ref] > after]
            return views.bounded(dict(contract='sico.terminal.commands.v1', commands=commands,
                next_after=cursor if more else None, observed_through=snapshot.sequence,
                integrity='metadata_only'), views.MAX_PAGE_JSON)

    def history(self, *, after=0, limit=views.MAX_COMMANDS):
        """Page command records with bounded request metadata for the record view."""
        position(after)
        views.limit(limit, views.MAX_COMMANDS)
        with self._snapshot() as snapshot:
            selected, stamps, count, cursor, more = {}, {}, 0, after, False
            for sequence, ref, value, timestamp in snapshot.events():
                if value['kind'] != 'accepted' or sequence <= after:
                    continue
                if count == limit:
                    more = True
                    break
                selected.setdefault(ref, None)
                stamps.setdefault(ref, timestamp)
                count, cursor = count + 1, sequence
            first = {}
            for sequence, ref, value, timestamp in snapshot.events():
                if ref in selected:
                    if value['kind'] == 'accepted':
                        first.setdefault(ref, sequence)
                        stamps.setdefault(ref, timestamp)
                    selected[ref] = snapshot.apply(selected[ref], value)
            if after > snapshot.sequence:
                raise ValueError('Terminal history cursor exceeds the snapshot')
            commands = [views.history_summary(recovered(state), stamps.get(ref, ''))
                        for ref, state in selected.items() if first[ref] > after]
            return views.history_page(commands, cursor if more else None,
                                      snapshot.sequence)

    def _command(self, snapshot, ref, *, offset=None, size=0, fragments=0):
        state, selected = None, []
        for _, candidate, value, _timestamp in snapshot.events():
            if candidate != ref:
                continue
            state = snapshot.apply(state, value)
            if offset is not None and value['kind'] == 'output':
                chunk = value['chunk']
                if (chunk['offset'] < offset + size and chunk['offset'] + chunk['size'] > offset
                        and len(selected) < fragments):
                    selected.append(value)
        if state is None:
            raise KeyError(ref)
        return recovered(state), selected

    def status(self, ref):
        self._ref(ref)
        with self._snapshot() as snapshot:
            state, _ = self._command(snapshot, ref)
            return views.bounded(dict(contract='sico.terminal.status.v1', command=views.summary(state),
                observed_through=snapshot.sequence, integrity='metadata_only'), views.MAX_RECEIPT_JSON)

    def request(self, ref, *, offset=0, limit=views.MAX_PAGE_BYTES):
        """Read exact admitted input separately from the compact status/list views."""
        self._ref(ref)
        position(offset)
        views.limit(limit, views.MAX_PAGE_BYTES)
        with self._snapshot() as snapshot:
            state, _ = self._command(snapshot, ref)
            return views.request_page(state.request, offset, limit, snapshot.sequence)

    def output(self, ref, *, offset=0, limit=views.MAX_PAGE_BYTES, fragments=views.MAX_FRAGMENTS):
        """Return lossless capture-order slices, verifying only touched attachments."""
        self._ref(ref)
        position(offset)
        views.limit(limit, views.MAX_PAGE_BYTES)
        views.limit(fragments, views.MAX_FRAGMENTS)
        with self._snapshot() as snapshot:
            state, selected = self._command(snapshot, ref, offset=offset, size=limit, fragments=fragments)
            if offset > state.output_bytes:
                raise ValueError('Terminal output cursor exceeds committed bytes')
            parts, cursor = [], offset
            for value in selected:
                chunk = OutputChunk.from_record(restore(value, self._reader)['chunk'])
                end = min(chunk.end, offset + limit)
                parts.append(views.fragment(chunk, cursor, end))
                cursor = end
            return views.bounded(dict(contract='sico.terminal.output.page.v1', command=views.summary(state),
                observed_through=snapshot.sequence, offset=offset, next_offset=cursor,
                has_more=cursor < state.output_bytes, fragments=parts,
                integrity='returned_chunks_verified'), views.MAX_PAGE_JSON)

    def receipt(self, ref, *, offset=0):
        return views.receipt(self.output(ref, offset=offset, limit=views.PREVIEW_BYTES,
                                         fragments=views.PREVIEW_FRAGMENTS))
