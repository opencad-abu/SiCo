"""Worker-owned, disposable disk index of chat rows derived from the journal."""

import json
import os
import sqlite3
import tempfile

from ..core.contracts import BoundContext
from .chat_projection import ChatProjection
from .event_display import EventDisplay
from ..core.session_events import SESSION_CONTRACT, EventCursor
from .published import freeze
from .workbench_budget import WorkbenchBudget

PAGE_ROWS = 60
PAGE_BYTES = 196608
# 归档在投影前就丢弃这两类行（见 sync）；同步时直接从索引跳过，不再解析。
ARCHIVE_DROPPED = frozenset({"model.status", "router.status"})


class IndexedChat(ChatProjection):
    def __init__(self, connection):
        self.connection = connection
        self.changed_rows = {}
        super().__init__(read_only=True)

    def changed(self, message):
        self.changed_rows[message['message_id']] = message

    def removed(self, message):
        key = message['message_id']
        self.changed_rows.pop(key, None)
        self.connection.execute('DELETE FROM messages WHERE id=?', (key,))

    def reverted(self, before_message_id):
        self.connection.execute('DELETE FROM messages WHERE id >= ?', (before_message_id,))
        self.changed_rows = {
            key: row for key, row in self.changed_rows.items()
            if key < before_message_id
        }

    def flush(self):
        self.connection.executemany('INSERT OR REPLACE INTO messages VALUES (?, ?)',
            ((key, json.dumps(row, ensure_ascii=False))
             for key, row in self.changed_rows.items()))
        self.changed_rows.clear()


class ChatArchive:
    """One subscription's canonical projection; paging never replays a task again."""

    def __init__(self, stream, validate=None):
        self.stream = stream
        self.validate = validate or stream.validate_current
        self.directory = tempfile.TemporaryDirectory(prefix='cad-chat-')
        path = os.path.join(self.directory.name, 'messages.sqlite')
        # SQLite resolves /proc/self/fd links to filenames. Use a private 0700
        # directory and an explicitly created 0600 file, then remove on close.
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        os.close(descriptor)
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA journal_mode=OFF')
        self.db.execute('PRAGMA cache_size=-2048')
        self.db.execute('CREATE TABLE messages (id INTEGER PRIMARY KEY, body TEXT NOT NULL)')
        self.projection = IndexedChat(self.db)
        self.display = EventDisplay()
        self.cursor = EventCursor(*stream.identity[:2],
            BoundContext(*stream.identity[2:], 'history', {}), history_sequence=stream.committed)
        self.rows = None
        if stream.controller is None:
            self.rows = iter(stream.events.iter_events())

    def close(self):
        if self.rows is not None and hasattr(self.rows, 'close'):
            self.rows.close()
        self.db.close()
        self.directory.cleanup()

    def _events(self, end):
        """消息相关行（展示噪声由索引跳过；关闭日志按顺序读取）。"""

        if self.rows is not None:
            while self.cursor.sequence < end:
                event = next(self.rows, None)
                if event is None:
                    raise ValueError('历史记录不完整，请重新打开会话')
                yield event
            return
        journal = self.stream.controller.journal
        after = self.cursor.sequence
        while after < end:
            limit = min(128, end - after)
            yield from journal.events(after, limit=limit, exclude=ARCHIVE_DROPPED)
            after += limit

    def sync(self, end):
        self.validate()
        if type(end) is not int or not self.cursor.sequence <= end <= self.stream.committed:
            raise ValueError('Invalid history watermark')
        envelope = dict(zip(('session_id', 'runtime_id', 'instance_id', 'generation'),
                            self.stream.identity))
        self.cursor.history_sequence = end
        for event in WorkbenchBudget(self.validate).rows(self._events(end)):
            self.cursor.skip(event["sequence"] - 1)
            accepted = self.cursor.consume(dict(envelope, contract=SESSION_CONTRACT,
                                                events=[event]))
            for raw in accepted:
                # Completed answers close the same streaming bubble in both
                # projections; status/delta rows never become pagination bounds.
                if raw['kind'] in {'model.status', 'router.status'}:
                    continue
                projected = self.display.project(raw)
                if projected is not None:
                    self.projection.receive(projected[0])
            self.projection.flush()
        self.cursor.skip(end)
        self.db.commit()
        self.validate()

    def page(self, before, after, end):
        if (type(before) is not int or type(after) is not int or min(before, after) < 0
                or (before and after)):
            raise ValueError('Invalid history page boundary')
        self.sync(end)
        clause, args, order = '', (), 'DESC'
        if before:
            clause, args = 'WHERE id < ?', (before,)
        elif after:
            clause, args, order = 'WHERE id > ?', (after,), 'ASC'
        rows, size = [], 0
        for key, body in self.db.execute(
                f'SELECT id, body FROM messages {clause} ORDER BY id {order} LIMIT ?',
                (*args, PAGE_ROWS)):
            cost = len(body.encode('utf-8'))
            if rows and size + cost > PAGE_BYTES:
                break
            rows.append(json.loads(body))
            size += cost
        if order == 'DESC':
            rows.reverse()
        low = rows[0]['message_id'] if rows else before or after
        high = rows[-1]['message_id'] if rows else before or after
        older = bool(self.db.execute('SELECT 1 FROM messages WHERE id < ? LIMIT 1',
                                    (low,)).fetchone())
        newer = bool(self.db.execute('SELECT 1 FROM messages WHERE id > ? LIMIT 1',
                                    (high,)).fetchone())
        return freeze(dict(rows=rows, before=before, after=after, end=end,
                           older=older, newer=newer))
