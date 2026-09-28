"""Server-owned replay subscriptions and scoped query sources."""

import threading
import uuid
from contextlib import ExitStack
from dataclasses import fields

from ..transport.framing import ProtocolError
from .frontend_assets import detail_record, input_record
from .frontend_session import SessionToken
from .replay import connection, prepare_preview, prepare_replay
from .service_protocol import exact_fields
from .service_query_dto import query_record, read_query
from .service_session_dto import ReplayCursor, SessionAddress
from .service_values import integer, name


class FrontendReplay:
    def __init__(self, workspace, descriptor):
        self.workspace, self.descriptor = workspace, descriptor
        self._lock = threading.Lock()
        self._feeds = {}
        self._closed = False

    def prepare(self, wire, params):
        exact_fields(params, {"session_id", "runtime_id", "activation", "messages", "readonly",
                              "resume"})
        name(params["session_id"])
        integer(params["activation"])
        integer(params["messages"])
        if type(params["readonly"]) is not bool or params["messages"] > 1000:
            raise ProtocolError("Invalid replay preparation")
        controller = self.workspace.controllers.get(params["session_id"])
        runtime = controller.runtime_id if controller else None
        if runtime != params["runtime_id"]:
            raise ValueError("会话运行实例已变化，请重新连接")
        after = None
        if params["resume"] is not None:
            resume = ReplayCursor.from_record(params["resume"])
            expected = SessionAddress(self.descriptor.project_id, self.descriptor.service_id,
                                      SessionToken(params["session_id"], runtime))
            if (resume.address != expected or resume.readonly or params["readonly"]
                    or resume.activation >= params["activation"] or controller is None
                    or resume.identity != connection(controller)):
                raise ProtocolError("Replay restart belongs to a different runtime or page")
            after = resume.sequence
        if params["readonly"]:
            future = self.workspace.events._request(prepare_preview, self.workspace,
                params["session_id"], runtime, params["activation"], params["messages"])
        else:
            if controller is None:
                raise ValueError("历史只读，不能隐式恢复")
            future = self.workspace.events._request(prepare_replay, controller,
                connection(controller), params["activation"], params["messages"], self.workspace,
                after)
        prepared = future.result()
        stream = prepared.stream
        with ExitStack() as pending:
            pending.callback(stream.close)
            return self._register(wire, params, controller, prepared, pending)

    def _register(self, wire, params, controller, prepared, pending):
        stream = prepared.stream
        runtime = params["runtime_id"]
        cursor = ReplayCursor(
            SessionAddress(self.descriptor.project_id, self.descriptor.service_id,
                           SessionToken(params["session_id"], runtime)),
            wire.identity.connection_id, uuid.uuid4().hex, params["activation"],
            *stream.identity[2:], stream.cursor.sequence, prepared.committed_sequence, -1,
            params["readonly"])
        reader = controller.journal if not params["readonly"] else stream.reader
        source = self.workspace.data.source(reader, stream if params["readonly"] else None,
                                             stream)
        pending.callback(source.close)
        with self._lock:
            if self._closed or wire.closed.is_set() or len(self._feeds) >= 64:
                raise ValueError("回放连接已结束或订阅已满")
            self._feeds[cursor.subscription_id] = (wire, cursor, stream, source)
        pending.pop_all()
        return dict(cursor=cursor.record(), context=getattr(prepared, "context", None))

    def resolve(self, wire, row, *, validate=True):
        cursor = ReplayCursor.from_record(row)
        with self._lock:
            entry = self._feeds.get(cursor.subscription_id)
        if entry is None:
            raise ValueError("回放订阅已关闭，请重新打开页面")
        owner, captured, stream, source = entry
        if (owner.closed.is_set() or wire.identity.client_id != owner.identity.client_id
                or cursor != captured):
            raise ProtocolError("Replay belongs to another client, connection or page")
        if validate:
            stream.validate_current()
        return captured, stream, source

    def events(self, wire, operation, params):
        if operation == "prepare":
            return self.prepare(wire, params)
        exact_fields(params, {"cursor"})
        cursor, stream, _source = self.resolve(wire, params["cursor"],
                                               validate=operation != "close")
        if wire.identity.connection_id != cursor.connection_id:
            raise ProtocolError("Event polling belongs to a retired connection")
        if operation == "close":
            self.remove(cursor.subscription_id)
            return None
        if operation != "batch":
            raise ProtocolError("Unknown replay operation")
        future = stream.request(self.workspace.events)
        batch = future.result()
        stream.received(future)
        return {field.name: getattr(batch, field.name) for field in fields(batch)}

    def query(self, wire, operation, params):
        schemas = {"pages": {"cursor", "query"},
                   "detail": {"cursor", "kind", "key", "compact", "parent"},
                   "processes": {"cursor", "keyword", "offset"},
                   "event_detail": {"cursor", "key", "offset"},
                   "chat_page": {"cursor", "before", "after", "end"},
                   "input_detail": {"cursor", "sequence", "index"}}
        if operation not in schemas:
            raise ProtocolError("Unknown scoped query")
        exact_fields(params, schemas[operation])
        cursor, stream, source = self.resolve(wire, params["cursor"])
        if operation == "pages":
            exact_fields(params["query"], {"activation", "value"})
            integer(params["query"]["activation"])
            query = read_query(params["query"]["value"], params["query"]["activation"])
            version, rows = source.query_pages(query).result()
            value = None if rows is None else {
                field.name: getattr(rows, field.name) for field in fields(rows)
                if field.name not in {"source", "query"}}
            if value is not None:
                value["query"] = query_record(rows.query)
            result = dict(version=version, rows=value)
        elif operation == "detail":
            result = source.detail(params["kind"], params["key"], compact=params["compact"],
                                   parent=params["parent"]).result()
            result = detail_record(result)
        elif operation == "processes":
            result = source.processes(params["keyword"], params["offset"]).result()
        elif operation == "chat_page":
            for key in ("before", "after", "end"):
                integer(params[key])
            result = source.chat_page(params["before"], params["after"], params["end"]).result()
        elif operation == "event_detail":
            result = self.workspace.data._request(stream.detail, params["key"],
                                                   params["offset"]).result()
        else:
            from .input_details import input_detail

            result = self.workspace.data._request(input_detail, stream, params["sequence"],
                                                   params["index"]).result()
            result = input_record(result)
        self.resolve(wire, params["cursor"])
        return dict(cursor=cursor.record(), value=result)

    def remove(self, subscription):
        with self._lock:
            entry = self._feeds.pop(subscription, None)
        if entry is not None:
            entry[2].close()
            entry[3].close()

    def retire(self, wire):
        with self._lock:
            keys = [key for key, value in self._feeds.items() if value[0] is wire]
        for key in keys:
            self.remove(key)

    def close(self):
        with self._lock:
            self._closed = True
            keys = tuple(self._feeds)
        for key in keys:
            self.remove(key)
