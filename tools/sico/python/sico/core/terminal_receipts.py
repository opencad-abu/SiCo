"""Bounded terminal query projections, without full command bodies or storage handles.

The control-authorized history list carries only a bounded command preview; model
receipts continue to omit command bodies and execution handles.
"""

import base64
import json

from .terminal_output import position

MAX_COMMANDS = 20
MAX_PAGE_BYTES = 16384
MAX_FRAGMENTS = 32
MAX_PAGE_JSON = 32768
MAX_RECEIPT_JSON = 8192
PREVIEW_BYTES = 768
PREVIEW_FRAGMENTS = 8
REASON_BYTES = 128
HISTORY_COMMAND_CHARS = 128


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')


def bounded(value, maximum):
    """Enforce the actual compact UTF-8 JSON wire budget, including escaping."""
    encoded = encode(value)
    if len(encoded) > maximum:
        raise ValueError('Terminal query response exceeds its encoded budget')
    return value


def limit(value, maximum):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError('Invalid terminal query limit')
    return value


def summary(state):
    """Capture completeness is a journal fact, not a check of every artifact."""
    reason = (state.reason or '').encode('utf-8')
    return dict(ref=state.request.ref.record(), phase=state.phase,
                exit_code=state.exit_code, signal=state.signal,
                cancel_requested=state.cancel_requested,
                output_complete=state.output_complete, output_bytes=state.output_bytes,
                output_sequence=state.output_sequence, input_bytes=state.input_bytes,
                reason=reason[:REASON_BYTES].decode('utf-8', errors='ignore') or None,
                reason_truncated=len(reason) > REASON_BYTES)


def history_summary(state, timestamp):
    """Read-only list row with safe request metadata and a bounded command preview.

    This projection is for the control-authorized terminal record view, not for
    model receipts. It never exposes the execution handle or full request body.
    """
    request = state.request
    text = request.text if request.text is not None else " ".join(request.argv)
    preview = text[:HISTORY_COMMAND_CHARS]
    row = summary(state)
    row.update(timestamp=str(timestamp or "")[:64], actor=request.actor,
               mode=request.mode, requested_cwd=request.cwd,
               execution_cwd=state.execution_cwd, task_id=request.task_id,
               command=preview, command_truncated=len(text) > HISTORY_COMMAND_CHARS)
    return row


def history_page(commands, next_after, observed_through):
    """Bounded command page for the read-only terminal record entry."""
    return bounded(dict(contract='sico.terminal.commands.v2', commands=commands,
                        next_after=next_after, observed_through=observed_through,
                        integrity='metadata_only'), MAX_PAGE_JSON)


def fragment(chunk, start, end):
    data = chunk.data[start - chunk.offset:end - chunk.offset]
    return dict(stream=chunk.stream, offset=start, encoding='base64',
                data=base64.b64encode(data).decode('ascii'))


def request_page(request, offset, maximum, observed_through):
    """Page exact request JSON bytes; command text/argv are never reinterpreted."""
    position(offset)
    limit(maximum, MAX_PAGE_BYTES)
    data = encode(request.record())
    if offset > len(data):
        raise ValueError('Terminal request cursor exceeds bytes')
    end = min(offset + maximum, len(data))
    return bounded(dict(contract='sico.terminal.request.page.v1', ref=request.ref.record(),
                        observed_through=observed_through, content_type='application/json',
                        encoding='base64', data=base64.b64encode(data[offset:end]).decode('ascii'),
                        offset=offset, next_offset=end, total_bytes=len(data), has_more=end < len(data)),
                   MAX_PAGE_JSON)


def receipt(page):
    """Render a short, explicitly lossy preview; exact bytes remain queryable."""
    previews = []
    for part in page['fragments']:
        data = base64.b64decode(part['data'], validate=True)
        try:
            text, lossy = data.decode('utf-8'), False
        except UnicodeDecodeError:
            text, lossy = data.decode('utf-8', errors='replace'), True
        previews.append(dict(stream=part['stream'], offset=part['offset'], text=text,
                             decoding_lossy=lossy))
    return bounded(dict(contract='sico.terminal.receipt.v1', command=page['command'],
                        observed_through=page['observed_through'], preview=previews,
                        preview_offset=page['offset'], next_offset=page['next_offset'],
                        has_more=page['has_more'], integrity=page['integrity']), MAX_RECEIPT_JSON)
