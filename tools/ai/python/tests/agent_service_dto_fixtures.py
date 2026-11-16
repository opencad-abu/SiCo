"""Captured wire identities and small existing workbench projections for DTO tests."""

from sico.service.frontend_session import SessionToken
from sico.service.service_query_dto import WorkbenchRequest
from sico.service.service_session_dto import ReplayCursor, SessionAddress
from sico.service.workbench_query import PageQuery, WorkbenchQuery
from sico.transport.framing import encode, strict_json


def cursor(**changes):
    address = SessionAddress("a" * 32, "b" * 32, SessionToken("query", "c" * 32))
    return ReplayCursor(**dict(dict(address=address, connection_id="d" * 32,
        subscription_id="e" * 32, activation=7, instance_id="i", generation="g",
        sequence=0, committed=100, state_version=0, readonly=False), **changes))


def request(**changes):
    defaults = dict(activation=7, query_id="q1", data=PageQuery(page_size=1),
                    report=PageQuery(page_size=1))
    return WorkbenchRequest(cursor(), WorkbenchQuery(**dict(defaults, **changes)))


def page():
    return dict(contract="copilot.workbench.query.v1", session_id="query", kind="data",
                version=100, query_id="q1", activation=7, total=1, offset=0, page_size=1,
                rows=[dict(id="row1", title="row", sequence=10)], selected_id="row1",
                target_found=False, locate_id="", sources=[], categories=[])


def roundtrip(row):
    return strict_json(encode(row)[4:])
