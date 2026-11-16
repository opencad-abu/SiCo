"""Test-only in-process frontend_queries harness for historical FB regression."""

from dataclasses import replace

from sico.service.frontend_receipt import project_receipt
from sico.service.replay_view import CursorPosition, ReplayView
from sico.service.workbench_publication import WorkbenchQueryPublication


class EventFeed:
    def __init__(self, stream):
        self.__stream = stream
        self.identity = stream.identity
        self.activation = stream.activation
        self.cursor = CursorPosition(stream.cursor.sequence)

    def close(self):
        self.__stream.close()
        if hasattr(self, '_chat_source'):
            self._chat_source.close()

    def received(self, receipt):
        self.__stream.received(receipt)

    def validate_current(self):
        self.__stream.validate_current()


class WorkbenchPort:
    def __init__(self, source, scope=None):
        self.__source = source
        self.scope = scope
        self.session_id = source.session_id
        self.identity = source.identity
        self.live_processes = source.live_processes

    def close(self):
        self.__source.close()

    def validate_current(self):
        self.__source.validate_current()

    def empty_query(self):
        return WorkbenchQueryPublication(self, identity=self.identity)

    def query_pages(self, request):
        def publish(value):
            version, rows = value
            return version, replace(rows, source=self) if rows is not None else None

        return project_receipt(self.__source.query_pages(request), publish)

    def detail(self, kind, key, *, compact=False, parent=None):
        return self.__source.detail(kind, key, compact=compact, parent=parent)

    def processes(self, keyword="", offset=0):
        return self.__source.processes(keyword, offset)


class FrontendQueries:
    """Backend-owned adapter; only its limited products cross into Qt."""

    def __init__(self, worker):
        self._worker = worker

    def stream(self, feed):
        if type(feed) is not EventFeed:
            raise TypeError("An EventFeed is required")
        stream = feed._EventFeed__stream
        stream.validate_current()
        if stream.sessions is not self._worker.sessions:
            raise ValueError("Event feed belongs to another desktop")
        return stream

    @staticmethod
    def publish(prepared):
        stream = prepared.stream
        if (stream.cursor is None or stream.cursor.identity != stream.identity
                or prepared.activation != stream.activation
                or (hasattr(prepared, "session_id") and
                    (prepared.session_id, prepared.runtime_id) != stream.identity[:2])):
            stream.close()
            raise ValueError("会话回放准备结果已失效")
        return ReplayView(EventFeed(prepared.stream), prepared.committed_sequence,
                          getattr(prepared, "context", None))

    def replay(self, token, activation, messages):
        return project_receipt(self._worker.prepare_replay(token, activation, messages),
                               self.publish, discard=lambda view: view.stream.close())

    def preview(self, session_id, activation, messages):
        return project_receipt(self._worker.preview_session(session_id, activation, messages),
                               self.publish, discard=lambda view: view.stream.close())

    def matches(self, prepared, session_id, activation, token=None):
        if type(prepared) is not ReplayView:
            return False
        try:
            stream = self.stream(prepared.stream)
            return (prepared.stream.identity == stream.identity
                    and prepared.stream.activation == stream.activation
                    and stream.session_id == session_id and stream.activation == activation
                    and (token is None or stream.identity[1] == token.runtime_id))
        except ValueError:
            return False

    def source(self, token, scope=None):
        owner = self._worker._tokens.resolve(token)
        stream = self.stream(scope) if scope is not None else None
        if stream is not None and stream.controller is not owner:
            raise ValueError("Workbench scope belongs to another session")
        source = self._worker.sessions.data.source(
            owner.journal, scope=stream, validate=lambda: self._worker._tokens.resolve(token))
        return WorkbenchPort(source, scope)

    def preview_source(self, prepared):
        if type(prepared) is not ReplayView:
            raise TypeError("A ReplayView is required")
        stream = self.stream(prepared.stream)
        if stream.controller is not None:
            raise ValueError("Expected a history preview")
        source = self._worker.sessions.data.source(stream.reader, stream, stream)
        return WorkbenchPort(source, prepared.stream)

    def batch(self, feed):
        return self._worker.event_batch(self.stream(feed))

    def detail(self, feed, key, offset=0):
        return self._worker.event_detail(self.stream(feed), key, offset)

    def chat_page(self, feed, before, after, end):
        stream = self.stream(feed)
        if not hasattr(feed, '_chat_source'):
            reader = stream.controller.journal if stream.controller else stream.reader
            feed._chat_source = self._worker.sessions.data.source(reader, scope=stream)
        return feed._chat_source.chat_page(before, after, end)

    def input_detail(self, feed, sequence, index=None):
        return self._worker.input_detail(self.stream(feed), sequence, index)
