"""Local workbench capabilities submit bounded queries to captured replay subscriptions."""

from dataclasses import fields, replace

from ..transport.framing import ProtocolError
from .frontend_assets import detail_value
from .remote_validation import query_publication
from .service_protocol import exact_fields
from .service_query_dto import query_record, read_query
from .service_values import integer
from .workbench_publication import WorkbenchQueryPublication


class RemoteSource:
    def __init__(self, api, token=None, scope=None):
        self._api, self._token = api, token
        self.scope = scope
        self.session_id = scope.identity[0] if scope else token.session_id
        self.identity = scope.identity if scope else (token.session_id, token.runtime_id)
        self.live_processes = scope is None or not scope._wire_cursor.readonly
        self._closed = False

    def close(self):
        self._closed = True

    def validate_current(self):
        if self._closed or self._api.detached:
            raise ValueError("Workbench source is closed")
        if self.scope is not None:
            self.scope.validate_current()

    def empty_query(self):
        return WorkbenchQueryPublication(self, identity=self.identity)

    def _request(self, operation, values, publish=lambda row: row):
        self.validate_current()
        epoch = self._api._queries.epoch

        def validate():
            self.validate_current()
            if self._api._queries.epoch != epoch:
                raise ValueError("查询连接已更换，请重新查询")

        def params():
            validate()
            if self.scope is None:
                raise ValueError("会话正在准备回放")
            return dict(cursor=self.scope._wire_cursor.record(), **values())

        def result(row):
            exact_fields(row, {"cursor", "value"})
            validate()
            if row["cursor"] != self.scope._wire_cursor.record():
                raise ProtocolError("Query result belongs to another subscription")
            return publish(row["value"])
        return self._api._queries.request(operation, params, result, validate=validate)

    def query_pages(self, request):
        def publish(row):
            exact_fields(row, {"version", "rows"})
            integer(row["version"])
            value = row["rows"]
            if value is None:
                return row["version"], None
            exact_fields(value, {f.name for f in fields(WorkbenchQueryPublication)} - {"source"})
            query = read_query(value["query"], request.activation)
            # The authoritative query normalizes deleted work/stage/task selections to "".
            scope_fields = ("work_id", "stage_id", "task_id")
            normalized = replace(request, **{key: getattr(query, key) for key in scope_fields})
            if query != normalized or any(getattr(query, key) not in ("", getattr(request, key))
                                          for key in scope_fields):
                raise ProtocolError("Query identity changed")
            query_publication(value, request, self.identity, row["version"])
            publication = WorkbenchQueryPublication(**dict(value, source=self,
                identity=self.identity, query=query))
            return row["version"], publication
        return self._request("pages", lambda: dict(query=dict(
            activation=request.activation, value=query_record(request))), publish)

    def detail(self, kind, key, *, compact=False, parent=None):
        return self._request("detail", lambda: dict(kind=kind, key=key, compact=compact,
                                                     parent=parent), detail_value)

    def processes(self, keyword="", offset=0):
        return self._request("processes", lambda: dict(keyword=keyword, offset=offset))
