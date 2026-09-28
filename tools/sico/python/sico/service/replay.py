"""Worker-owned replay preparation, scoped to a runtime and page activation."""

from dataclasses import dataclass

from ..core.contracts import BoundContext
from ..storage.history import SessionReader
from ..storage.index import CONTEXT_KINDS
from .event_stream import EventStream
from .events import SESSION_CONTRACT, EventCursor
from .preview_source import PreviewSource
from .published import freeze


def connection(controller):
    context = controller.current
    return (controller.session_id, controller.runtime_id,
            context.instance_id, context.generation)


@dataclass(frozen=True)
class PreparedReplay:
    controller: object
    activation: int
    stream: object
    committed_sequence: int

    @property
    def cursor(self):
        return self.stream.cursor

    def matches(self, controller, activation):
        return (self.controller is controller and self.activation == activation
                and self.cursor is not None and self.cursor.identity == connection(controller)
                and not controller.closing and not controller._shutdown.is_set())


def prepare_replay(controller, identity, activation, messages, sessions=None, after=None):
    # The controller -> journal lock order matches read_updates/commands. Only
    # this worker may wait; the front end only enqueues and polls its Future.
    with controller._lock:
        if (connection(controller) != identity or controller.closing
                or controller._shutdown.is_set()):
            raise ValueError("会话运行实例已失效，请重新打开会话")
        start, committed, origins = controller.journal.prepare_replay(messages, after=after)
        cursor = EventCursor(*identity[:2], controller.current,
                             history_sequence=controller.history_sequence)
        cursor.sequence = start - 1
        cursor.native_origins = origins
        stream = EventStream(controller, cursor, activation, committed)
        stream.sessions = sessions
        if sessions is not None:
            sessions.events.own(stream)
        return PreparedReplay(controller, activation, stream, committed)


@dataclass(frozen=True)
class PreparedPreview:
    session_id: str
    runtime_id: object
    activation: int
    reader: object
    events: object
    context: dict
    committed_sequence: int
    stream: object = None

    def matches(self, sessions, session_id, activation):
        controller = sessions.controllers.get(session_id)
        runtime = controller.runtime_id if controller is not None else None
        return (self.session_id == session_id and self.activation == activation
                and self.runtime_id == runtime
                and (controller is None or (not controller.closing
                     and not controller._shutdown.is_set())))


@dataclass(frozen=True)
class PreviewWindow:
    """Read-only replay window: where to start, and what the prefix implies."""

    start: int
    offset: int
    committed: int
    context: dict
    deleted: bool
    origins: object = None


def _open_window(journal, messages):
    """Window for a session this window already owns; the journal is in memory."""

    with journal._events_lock:
        start = journal.replay_start(messages) if messages else 1
        committed = len(journal.records)
        deleted = journal.deleted
        context = {}
        for row in reversed(journal.records):
            if row["kind"] not in {"session.created", "task.started"}:
                continue
            value = row["payload"].get("context")
            if isinstance(value, dict):
                context = value
                break
    return PreviewWindow(start, 0, committed, context, deleted, journal.native_origins(start - 1))


def _closed_window(reader, messages):
    """Window for a closed journal: located by reading the file from its end."""

    window = reader.tail_window(messages)
    origins = reader.origins_before(window.offset) if window.start > 1 else None
    return PreviewWindow(window.start, window.offset, window.committed, window.context,
                         window.deleted, origins)


def _open_task_window(journal):
    """First screen for a session this window already owns: the newest task."""

    with journal._events_lock:
        records = journal.records
        start = records[0]["sequence"] if records else 1
        for row in reversed(records):
            if row["kind"] == "task.started":
                start = row["sequence"]
                break
        committed = len(records)
        deleted = journal.deleted
        context = {}
        for row in reversed(records):
            if row["kind"] not in CONTEXT_KINDS:
                continue
            value = row["payload"].get("context")
            if isinstance(value, dict):
                context = value
                break
    return PreviewWindow(start, 0, committed, context, deleted,
                         journal.native_origins(start - 1))


def _closed_task_window(reader):
    """First screen for a closed journal: the newest task, located by index."""

    window = reader.tail_task_window()
    origins = reader.origins_before(window.offset) if window.start > 1 else None
    return PreviewWindow(window.start, window.offset, window.committed, window.context,
                         window.deleted, origins)


def _full_window(reader, runtime):
    """Legacy whole-journal walk, kept for callers without a message window."""

    context, committed = {}, 0
    bound = BoundContext("preview", "preview", "preview", {})
    validator = EventCursor(reader.session_id, runtime, bound, history_sequence=2 ** 63 - 1)
    for event in reader.stream_events():
        validator.consume({
            "contract": SESSION_CONTRACT, "session_id": reader.session_id, "runtime_id": runtime,
            "instance_id": bound.instance_id, "generation": bound.generation,
            "events": [event],
        })
        if event["kind"] == "codex.thread.deleted":
            raise ValueError("此会话已删除")
        if event["kind"] in {"session.created", "task.started"}:
            context = event["payload"].get("context", context)
        committed = event["sequence"]
    return PreviewWindow(1, 0, committed, context, False, None)


def prepare_preview(sessions, session_id, runtime, activation, messages=0, task_window=False):
    controller = sessions.controllers.get(session_id)
    if runtime != (controller.runtime_id if controller is not None else None):
        raise ValueError("历史会话运行实例已变化，请重新选择会话")
    reader = SessionReader(sessions.root, session_id)
    from .damaged_record import require_readable

    require_readable(reader)
    journal = controller.journal if controller is not None else None
    source = PreviewSource(reader, journal)
    if task_window:
        window = (_open_task_window(journal) if journal is not None
                  else _closed_task_window(reader))
        if window.deleted:
            raise ValueError("此会话已删除")
    elif messages:
        window = (_open_window(journal, messages) if journal is not None
                  else _closed_window(reader, messages))
        if window.deleted:
            raise ValueError("此会话已删除")
    else:
        window = _open_window(journal, 0) if journal is not None else _full_window(reader, runtime)
    source.committed = window.committed
    bound = BoundContext.from_record(window.context) if window.context else BoundContext(
        "preview", "preview", "preview", {},
    )
    cursor = EventCursor(session_id, runtime, bound, history_sequence=window.committed)
    # Start the cursor at the window boundary and seed the native-origin index
    # from the prefix, so validation stays exact without replaying it.
    cursor.sequence = window.start - 1
    if window.origins is not None:
        cursor.native_origins = window.origins
    stream = EventStream(None, cursor, activation, window.committed, reader=reader, events=source,
                         preview_offset=window.offset)
    stream.sessions, stream.preview_owner = sessions, controller
    result = PreparedPreview(session_id, runtime, activation, reader, stream,
                             freeze(window.context), window.committed, stream)
    if not result.matches(sessions, session_id, activation):
        raise ValueError("历史会话运行实例已变化，请重新选择会话")
    sessions.events.own(stream)
    return result
