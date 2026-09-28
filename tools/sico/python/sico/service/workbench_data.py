"""Background workbench queries and verified, on-demand detail content."""

from __future__ import annotations

import queue
import threading
from collections import deque
from concurrent.futures import Future

from ..core.contracts import BoundContext
from ..storage.journal import SessionJournal
from ..storage.workbench import WorkbenchIndex
from ..transport.framing import strict_json
from .detail_content import DetailContent
from .display import data_html
from .events import SESSION_CONTRACT, EventCursor
from .native_display import native_html
from .process_data import ProcessIndex
from .published import FrozenDict, thaw
from .schematic_preview import preview_images
from .tool_display import result_html
from .workbench_budget import WorkbenchBudget
from .workbench_publication import WorkbenchQueryPublication, publish_query
from .workbench_query import WorkbenchQuery
from .workbench_snapshot import WorkbenchSnapshot, publish_snapshot, record_event


class DetailLinkError(ValueError):
    """A passive link is not registered in the requested evidence scope."""


class WorkbenchSource:
    """Opaque source; its index and queries belong to the data worker."""

    def __init__(self, service, reader, events=None, scope=None, validate=None):
        self.service, self.session_id = service, reader.session_id
        self._reader, self._events, self._index = reader, events, None
        self._version = 0
        self._records = deque(maxlen=200)
        self.scope = scope
        self._validate = validate
        self.identity = scope.identity if scope is not None else (self.session_id, None)
        self._processes = ProcessIndex(reader)
        self._closed = threading.Event()
        self._fault = None
        self._cursor = None
        self._chat = None

    def chat_page(self, before, after, end):
        return self.service._request(self._chat_page, before, after, end)

    def _chat_page(self, before, after, end):
        from .chat_archive import ChatArchive

        self.validate_current()
        if self.scope is None:
            raise ValueError("History requires a prepared replay")
        if self._chat is None:
            self._chat = ChatArchive(self.scope, self.validate_current)
        try:
            result = self._chat.page(before, after, end)
        except Exception:
            # The index is disposable. Never reuse a partially applied event.
            self._chat.close()
            self._chat = None
            raise
        self.validate_current()
        return result

    @property
    def live_processes(self):
        return isinstance(self._reader, SessionJournal)

    def processes(self, keyword="", offset=0):
        return self.service._request(self._process_page, keyword, offset)

    def _process_page(self, keyword, offset):
        self._sync()
        result = self._processes.page(self.live_processes, keyword, offset)
        self.validate_current()
        return result

    def close(self):
        self._closed.set()

    def validate_current(self):
        if self._validate is not None:
            self._validate()
        if self._closed.is_set() or self.service._closing:
            raise ValueError("Workbench source is closed")
        if self._fault is not None:
            raise ValueError("Workbench index requires reopening") from self._fault
        if self.scope is not None:
            if self.scope.identity != self.identity or self.identity[0] != self.session_id:
                raise ValueError("Workbench source identity changed")
            self.scope.validate_current()

    def empty(self):
        return WorkbenchSnapshot(self)

    def empty_query(self):
        return WorkbenchQueryPublication(self, identity=self.identity)

    def snapshot(self, version=0):
        """Compatibility API for complete, detached presentation snapshots."""
        return self.service._request(self._snapshot, version)

    def query_pages(self, request):
        if not isinstance(request, WorkbenchQuery):
            raise ValueError("Invalid workbench query")
        return self.service._request(self._query_pages, request)

    def detail(self, kind, key, *, compact=False, parent=None):
        return self.service._request(self._detail, kind, key, compact, parent)

    def _live_events(self, after):
        end = None
        while end is None or after < end:
            rows, committed = self._reader.committed_batch(after, 512, 262144)
            end = committed if end is None else end
            if not rows:
                if after < end:
                    raise ValueError("Workbench journal lost committed events")
                break
            for row in rows:
                if row["sequence"] > end:
                    return
                yield row
                after = row["sequence"]

    def _sync(self):
        self.validate_current()
        try:
            self._sync_events()
        except Exception as exc:
            self._fault = exc
            raise
        self.validate_current()

    def _sync_events(self):
        if self._index is None:
            self._index = WorkbenchIndex(self._reader)
            if self.scope is not None:
                owner = self.scope.controller
                self._cursor = EventCursor(
                    *self.identity[:2], BoundContext(*self.identity[2:], "workbench", {}),
                    history_sequence=owner.history_sequence if owner else self.scope.committed,
                )
        index = self._index
        if self._events is not None:
            events, self._events = self._events, ()
            if hasattr(events, "iter_events"):
                events = events.iter_events()
        elif isinstance(self._reader, SessionJournal):
            events = self._live_events(index.sequence)
        else:
            events, self._events = self._reader.stream_events(index.sequence), ()
        for event in WorkbenchBudget(self.validate_current).rows(events):
            self.validate_current()
            if isinstance(event, FrozenDict):
                event = thaw(event)
            if event["sequence"] != index.sequence + 1:
                raise ValueError("Workbench event sequence is incomplete or duplicated")
            if self._cursor is not None:
                batch = dict(zip(("session_id", "runtime_id", "instance_id", "generation"),
                                 self.identity))
                self._cursor.consume({**batch, "contract": SESSION_CONTRACT, "events": [event]})
            index.receive(event)
            self._processes.receive(event)
            kind = event["kind"]
            if kind != "model.delta":
                self._version = event["sequence"]
            record = record_event(event)
            if record is not None:
                self._records.append(record)

    def _snapshot(self, version):
        self._sync()
        if version == self._version:
            return self._version, None
        snapshot = publish_snapshot(self)
        self.validate_current()
        return self._version, snapshot

    def _query_pages(self, request):
        request.validate()
        self._sync()
        result = publish_query(self, request)
        self.validate_current()
        return self._version, result

    def _detail(self, kind, key, compact, parent):
        self._sync()
        if kind == "process":
            result = self._processes.detail(key, self.live_processes)
            self.validate_current()
            return result
        try:
            target = self._index.resolve(kind, key)
            if parent and parent[0] == "report":
                report = self._index.resolve(*parent)
                if (kind == "data" and key not in {ref["id"] for ref in report["evidence"]}
                        or kind == "report" and target["work_id"] != report["work_id"]):
                    raise ValueError("Link is not a published report reference")
        except ValueError as exc:
            raise DetailLinkError(str(exc)) from exc
        result = DetailContent(self._index, compact=compact).prepare(kind, key)
        self.validate_current()
        return result


class WorkbenchService:
    """Bounded data queue independent of task control, submit and audit replies."""

    def __init__(self, *, name="copilot-data"):
        self._queue = queue.Queue(maxsize=64)
        self._lock = threading.Lock()
        self._pending = 0
        self._closing = False
        self._sources = []
        self._stopped = threading.Event()
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._thread.start()

    def source(self, reader, events=None, scope=None, validate=None):
        source = WorkbenchSource(self, reader, events, scope, validate)
        with self._lock:
            if self._closing or len(self._sources) >= 64:
                raise ValueError("Workbench source capacity is unavailable")
            self._sources.append(source)
        return source

    @property
    def busy(self):
        with self._lock:
            return bool(self._pending) or (self._closing and not self._stopped.is_set())

    def _request(self, operation, *args):
        future = Future()
        with self._lock:
            if self._closing:
                raise ValueError("Workbench is closing")
            try:
                self._queue.put_nowait((future, operation, args))
            except queue.Full:
                raise ValueError("Workbench request queue is full") from None
            self._pending += 1
        return future

    def tool_result(self, reader, result, *, context=False):
        return self._request(self._tool_result, reader, result, context)

    @staticmethod
    def _tool_result(reader, result, context):
        if result.get("truncated") and isinstance(result.get("artifact"), dict):
            artifact = result["artifact"]
            try:
                if not 0 < artifact["size"] <= 262144:
                    raise ValueError("Large result")
                result = strict_json(reader.artifact_text(
                    artifact["path"], artifact["sha256"], max_bytes=262144))
            except (ValueError, OSError, KeyError, TypeError):
                return {"notice": "完整结果暂不可读；数据明细中保留了归档引用。",
                        "fields": data_html(result)}
        data = result.get("data")
        if isinstance(data, dict) and data.get("contract") == "codex.operation.v1":
            try:
                summary = native_html(data, reader)
            except (ValueError, OSError, KeyError, TypeError):
                return {"notice": "原生输出暂不可读或校验失败。", "fields": data_html(result)}
            return {"summary": summary, "fields": data_html(result)}
        images, preview_notice = preview_images(result, reader)
        return {"summary": result_html(result, context=context), "fields": data_html(result),
                "images": images, "preview_notice": preview_notice}

    def _run(self):
        try:
            while True:
                try:
                    item = self._queue.get(timeout=0.05)
                except queue.Empty:
                    self._idle()
                    continue
                if item is None:
                    return
                future, operation, args = item
                try:
                    if future.set_running_or_notify_cancel():
                        try:
                            future.set_result(operation(*args))
                        except Exception as exc:
                            future.set_exception(exc)
                finally:
                    with self._lock:
                        self._pending -= 1
                    self._idle()
        finally:
            self._idle()
            self._stopped.set()

    def _idle(self):
        with self._lock:
            retired = [s for s in self._sources if s._closed.is_set() or self._closing]
            self._sources = [s for s in self._sources if s not in retired]
        # Releasing a large index belongs to this worker, outside the Qt queue lock.
        for source in retired:
            source.close()
            if source._chat is not None:
                source._chat.close()
                source._chat = None
            source._index = source._events = source._fault = source._cursor = None
            source._records.clear()
            source._processes.rows.clear()
            source._processes.bindings.clear()

    def close(self):
        cancelled = []
        with self._lock:
            if self._closing:
                return
            self._closing = True
            while True:
                try:
                    future, _, _ = self._queue.get_nowait()
                except queue.Empty:
                    break
                cancelled.append(future)
                self._pending -= 1
            self._queue.put_nowait(None)
        for future in cancelled:
            future.cancel()

    def wait(self, timeout=None):
        self._thread.join(timeout)
        return self._stopped.is_set()
