"""Validate and splice bounded history pages without moving the reading anchor."""

from sico.service.event_display import encoded
from PyQt5.QtCore import QPoint
from PyQt5.QtGui import QTextCursor


def validate_page(page, request):
    if (not isinstance(page, dict) or any(page.get(k) != v for k, v in request.items())
            or type(page.get('older')) is not bool or type(page.get('newer')) is not bool
            or not isinstance(page.get('rows'), (list, tuple)) or len(page['rows']) > 60):
        raise ValueError('Invalid history page')
    last = 0
    for row in page['rows']:
        key = row.get('message_id')
        if (type(key) is not int or key <= last or not isinstance(row.get('text'), str)
                or type(row.get('revision')) is not int
                or row.get('role') not in {'user', 'assistant', 'tools', 'native', 'context',
                                         'notice', 'audit', 'report', 'result', 'activity'}
                or (request['before'] and key >= request['before'])
                or (request['after'] and key <= request['after'])):
            raise ValueError('History messages are out of order')
        last = key


def cache_window(owner, rows, anchor_id, direction):
    """Keep the anchor plus contiguous neighbours, favouring the requested side."""
    pivot = next(i for i, row in enumerate(rows) if row['message_id'] == anchor_id)
    low = high = pivot
    size, chars = len(encoded(rows[pivot])), owner.history._message_size(rows[pivot])
    sides = (range(pivot - 1, -1, -1), range(pivot + 1, len(rows)))
    if direction == 'after':
        sides = sides[::-1]
    for side in sides:
        for index in side:
            cost, shown = len(encoded(rows[index])), owner.history._message_size(rows[index])
            if (high - low + 1 >= owner.CACHE_ROWS or size + cost > owner.CACHE_BYTES
                    or chars + shown > owner.CACHE_CHARS):
                break
            low, high = min(low, index), max(high, index)
            size, chars = size + cost, chars + shown
    owner.older |= low > 0
    owner.newer |= high < len(rows) - 1
    return rows[low:high + 1]


def prepend(history, view, rows):
    """Insert at the document root while Qt retains cursors into existing frames."""
    if not rows:
        return
    document = view.document()
    cursor = QTextCursor(document)
    before = document.characterCount()
    positions = []
    cursor.beginEditBlock()
    try:
        for row in rows:
            positions.append(cursor.position())
            history._insert(cursor, row, history.username, read_only=history.read_only,
                            base_point=max(10, view.font().pointSize()))
    finally:
        cursor.endEditBlock()
    added = document.characterCount() - before
    history._positions = positions + [p + added for p in history._positions]
    history._rendered = [(m['message_id'], m['revision'], len(m['text'] + m.get('suffix', '')))
                         for m in rows] + history._rendered


def merge_page(owner, page, direction):
    history, view = owner.history, owner.window.display
    anchor = view.cursorForPosition(QPoint(0, 0))
    top = view.cursorRect(anchor).top()
    index = max((i for i, p in enumerate(history._positions) if p <= anchor.position()), default=0)
    anchor_id = history._rendered[index][0]
    known = {row['message_id'] for row in history.messages}
    rows = [row for row in page['rows'] if row['message_id'] not in known]
    if direction == 'before':
        owner.older = page['older']
        merged = rows + history.messages
    else:
        owner.newer = page['newer']
        merged = history.messages + rows
    history.messages = cache_window(owner, merged, anchor_id, direction)
    if direction == 'before':
        prepend(history, view, [row for row in history.messages if row['message_id'] not in known])
    owner.end = page['end']
    history.dirty = True
    history.render(view, force=True)
    bar = view.verticalScrollBar()
    bar.setValue(bar.value() + view.cursorRect(anchor).top() - top)
