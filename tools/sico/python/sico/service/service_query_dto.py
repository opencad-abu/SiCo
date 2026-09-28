"""Wire projection of the existing workbench query model, with opaque source binding."""

from dataclasses import dataclass, fields

from ..transport.framing import ProtocolError
from .service_protocol import exact_fields
from .service_session_dto import ReplayCursor
from .service_values import integer, json_view, name
from .workbench_query import PageQuery, WorkbenchQuery

PAGE_FIELDS = {item.name for item in fields(PageQuery)}
QUERY_FIELDS = {item.name for item in fields(WorkbenchQuery)} - {"activation"}
PAGE_RESULT_FIELDS = {"contract", "session_id", "kind", "version", "query_id", "activation",
                      "total", "offset", "page_size", "rows", "selected_id", "target_found",
                      "locate_id", "sources", "categories"}


def query_record(query):
    # Activation has one representation on the wire: the subscription cursor.
    row = {key: getattr(query, key) for key in QUERY_FIELDS - {"data", "report"}}
    for kind in ("data", "report"):
        row[kind] = {key: getattr(getattr(query, kind), key) for key in PAGE_FIELDS}
    return row


def read_query(row, activation):
    exact_fields(row, QUERY_FIELDS)
    pages = {}
    for kind in ("data", "report"):
        exact_fields(row[kind], PAGE_FIELDS)
        pages[kind] = PageQuery(**row[kind])
    return WorkbenchQuery(activation=activation, **dict(row, **pages))


@dataclass(frozen=True)
class WorkbenchRequest:
    cursor: ReplayCursor
    query: WorkbenchQuery

    def __post_init__(self):
        if type(self.cursor) is not ReplayCursor or type(self.query) is not WorkbenchQuery:
            raise ProtocolError("Invalid workbench request DTO")
        try:
            self.query.validate()
        except ValueError as exc:
            raise ProtocolError("Invalid workbench query") from exc
        name(self.query.query_id)
        if self.query.activation != self.cursor.activation:
            raise ProtocolError("Workbench query belongs to another page activation")
        for page in (self.query.data, self.query.report):
            if type(page) is not PageQuery:
                raise ProtocolError("Invalid workbench page model")
            integer(page.offset)
            integer(page.version, -1)

    def record(self):
        """Backend-only serialization; construction on Qt performs bounded field checks."""
        return json_view(dict(cursor=self.cursor.record(), query=query_record(self.query)))

    @classmethod
    def from_record(cls, row):
        exact_fields(row, {"cursor", "query"})
        cursor = ReplayCursor.from_record(row["cursor"])
        return cls(cursor, read_query(row["query"], cursor.activation))


@dataclass(frozen=True)
class WorkbenchPage:
    """One bounded page. Oversize pages fail explicitly; no silent row truncation.

    Server/source ownership and large-detail chunking are integrated in AS-04.
    This DTO carries the existing query_page projection, never its source object.
    """

    request: WorkbenchRequest
    page: dict

    def __post_init__(self):
        if type(self.request) is not WorkbenchRequest:
            raise ProtocolError("A captured workbench request is required")
        page = self.page
        exact_fields(page, PAGE_RESULT_FIELDS)
        kind = page["kind"]
        if kind not in ("data", "report"):
            raise ProtocolError("Unknown workbench page kind")
        query = self.request.query
        source = self.request.cursor
        selection = getattr(query, kind)
        for key in ("version", "total", "offset", "activation", "page_size"):
            integer(page[key])
        if (page["contract"] != "copilot.workbench.query.v1"
                or page["session_id"] != source.address.session.session_id
                or page["activation"] != source.activation or page["query_id"] != query.query_id
                or page["page_size"] != selection.page_size
                or page["locate_id"] != selection.locate_id
                or type(page["target_found"]) is not bool
                or not isinstance(page["selected_id"], str)
                or not isinstance(page["rows"], (list, tuple))
                or not isinstance(page["sources"], (list, tuple))
                or not isinstance(page["categories"], (list, tuple))
                or any(not isinstance(value, str) for value in
                       (*page["sources"], *page["categories"]))
                or any(not isinstance(row, dict) for row in page["rows"])
                or len(page["rows"]) > selection.page_size
                or page["version"] < source.state_version
                or page["offset"] % selection.page_size != 0
                or page["offset"] + len(page["rows"]) > page["total"]):
            raise ProtocolError("Inconsistent workbench page response")
        published = json_view(self.record())
        object.__setattr__(self, "page", published["page"])

    def record(self):
        return dict(request=self.request.record(), page=self.page)

    @classmethod
    def from_record(cls, row, expected):
        exact_fields(row, {"request", "page"})
        request = WorkbenchRequest.from_record(row["request"])
        if request != expected:
            raise ProtocolError("Workbench response belongs to another request or page")
        return cls(request, row["page"])

    @classmethod
    def from_publication(cls, publication, request, kind):
        if (publication.identity != request.cursor.identity
                or publication.query.query_id != request.query.query_id
                or publication.query.activation != request.query.activation):
            raise ProtocolError("Workbench publication belongs to another source")
        return cls(request, publication.pages[kind])
