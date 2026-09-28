"""Validate remote display publications before exposing them to Qt."""

from ..core.contracts import BoundContext
from ..transport.framing import ProtocolError
from .event_display import BATCH_BYTES, BATCH_EVENTS, EVENT_BYTES, encoded
from .service_protocol import exact_fields
from .service_query_dto import PAGE_RESULT_FIELDS
from .service_values import integer
from .state_publication import STATE_BYTES


def session_view(row):
    for key in ("label", "base", "model", "display_name"):
        if not isinstance(row[key], str) or len(row[key]) > 4096:
            raise ProtocolError("Invalid session display field")
    if (type(row["resources"]) is not bool or type(row["closing"]) is not bool
            or row["activity"] not in ("idle", "active", "pending")):
        raise ProtocolError("Invalid session display state")


def display_batch(batch, identity):
    for key in ("activation", "start", "end", "committed", "size"):
        integer(getattr(batch, key))
    if (not isinstance(batch.error, str) or len(batch.error) > 4096
            or not isinstance(batch.events, (tuple, list))
            or len(batch.events) > BATCH_EVENTS or batch.size > BATCH_BYTES):
        raise ProtocolError("Invalid display batch budget")
    previous, size = batch.start - 1, 0
    for event in batch.events:
        if not isinstance(event, dict):
            raise ProtocolError("Invalid display event")
        integer(event.get("sequence"), 1)
        if (event.get("session_id") != identity[0]
                or not previous < event["sequence"] <= batch.end
                or not isinstance(event.get("kind"), str)
                or not isinstance(event.get("payload"), dict)):
            raise ProtocolError("Frontend event sequence mismatch")
        cost = len(encoded(event))
        if cost > EVENT_BYTES:
            raise ProtocolError("Display event exceeds its budget")
        size += cost
        previous = event["sequence"]
    if size != batch.size:
        raise ProtocolError("Display batch size mismatch")
    if batch.snapshot is None:
        return
    snapshot = batch.snapshot
    if (not isinstance(snapshot, (list, tuple)) or len(snapshot) != 2
            or not isinstance(snapshot[0], dict)
            or not isinstance(snapshot[1], (list, tuple)) or len(snapshot[1]) != 2
            or any(not isinstance(c, BoundContext) for c in snapshot[1])):
        raise ProtocolError("Invalid state snapshot")
    state, contexts = snapshot
    integer(state.get("sequence"))
    integer(state.get("version"))
    if (len(encoded(state)) > STATE_BYTES
            or state.get("context") != contexts[0].record()
            or state.get("default_context") != contexts[1].record()
            or (contexts[0].instance_id, contexts[0].generation) != identity[2:]):
        raise ProtocolError("Invalid snapshot source or budget")


def query_publication(value, request, identity, version):
    request.validate()
    integer(value["version"])
    if (value["version"] != version or value["contract"] != "copilot.workbench.pages.v1"
            or tuple(value["identity"]) != identity):
        raise ProtocolError("Invalid workbench publication version or source")
    exact_fields(value["pages"], {"data", "report"})
    for kind, page in value["pages"].items():
        exact_fields(page, PAGE_RESULT_FIELDS)
        selection = getattr(request, kind)
        for key in ("version", "total", "offset", "activation", "page_size"):
            integer(page[key])
        if (page["contract"] != "copilot.workbench.query.v1" or page["kind"] != kind
                or page["session_id"] != identity[0] or page["version"] != version
                or page["activation"] != request.activation or page["query_id"] != request.query_id
                or page["page_size"] != selection.page_size
                or page["locate_id"] != selection.locate_id
                or type(page["target_found"]) is not bool
                or not isinstance(page["selected_id"], str)
                or not isinstance(page["rows"], (list, tuple))
                or len(page["rows"]) > selection.page_size
                or any(not isinstance(row, dict) for row in page["rows"])
                or page["offset"] % selection.page_size
                or page["offset"] + len(page["rows"]) > page["total"]):
            raise ProtocolError("Invalid workbench page")
        for key in ("sources", "categories"):
            if (not isinstance(page[key], (list, tuple)) or len(page[key]) > 2000
                    or any(not isinstance(item, str) for item in page[key])):
                raise ProtocolError("Invalid workbench options")
    for key in ("works", "stages", "tasks", "audits"):
        if not isinstance(value[key], dict) or len(value[key]) > 2000:
            raise ProtocolError("Invalid workbench scope inventory")
    if not isinstance(value["records"], (tuple, list)) or len(value["records"]) > 200:
        raise ProtocolError("Invalid workbench records")
