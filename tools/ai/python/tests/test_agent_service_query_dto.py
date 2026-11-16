"""Query DTOs reuse workbench filtering models and publish no reader/source capabilities."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from agent_service_dto_fixtures import cursor, page, request, roundtrip
from agent_workbench_query_fixtures import History

from sico.service.service_query_dto import WorkbenchPage, WorkbenchRequest
from sico.service.service_values import json_view
from sico.service.workbench_data import WorkbenchService
from sico.transport.framing import ProtocolError


def test_query_uses_the_existing_model_and_activation_is_encoded_once():
    value = request()
    row = roundtrip(value.record())
    assert "activation" not in row["query"]
    assert WorkbenchRequest.from_record(row) == value
    with pytest.raises(ProtocolError):
        replace(value, query=replace(value.query, activation=8))


@pytest.mark.parametrize("changes", [
    {"offset": True}, {"offset": -1}, {"offset": 2**63}, {"page_size": 201},
    {"page_size": 0}, {"version": -2}, {"keyword": "x" * 257}, {"sort": "random"},
    {"category": "imaginary"}, {"source": {}}, {"selected_id": object()},
])
def test_query_wire_constraints_are_stricter_than_python_duck_typing(changes):
    row = roundtrip(request().record())
    row["query"]["data"].update(changes)
    with pytest.raises(ProtocolError):
        WorkbenchRequest.from_record(row)


@pytest.mark.parametrize("field", ["source", "reader", "future", "activation"])
def test_query_schema_rejects_resource_or_redundant_fields(field):
    row = roundtrip(request().record())
    row["query"][field] = None
    with pytest.raises(ProtocolError):
        WorkbenchRequest.from_record(row)


@pytest.mark.parametrize("change", ["query", "runtime", "service", "activation", "subscription",
                                   "connection", "watermark", "filter"])
def test_late_query_reply_must_match_the_entire_captured_request(change):
    expected = request()
    old = WorkbenchPage(expected, page()).record()
    source = expected.cursor
    options = {
        "query": replace(expected, query=replace(expected.query, query_id="q2")),
        "runtime": replace(expected, cursor=replace(source, address=replace(source.address,
                           session=replace(source.address.session, runtime_id="f" * 32)))),
        "service": replace(expected, cursor=replace(source, address=replace(source.address,
                                                                     service_id="f" * 32))),
        "activation": replace(expected, cursor=replace(source, activation=8),
                              query=replace(expected.query, activation=8)),
        "subscription": replace(expected, cursor=replace(source, subscription_id="f" * 32)),
        "connection": replace(expected, cursor=replace(source, connection_id="f" * 32)),
        "watermark": replace(expected, cursor=replace(source, committed=101)),
        "filter": replace(expected, query=replace(expected.query, task_id="other")),
    }
    with pytest.raises(ProtocolError, match="another request"):
        WorkbenchPage.from_record(roundtrip(old), options[change])


@pytest.mark.parametrize("field,value", [
    ("version", True), ("total", -1), ("offset", 2), ("query_id", "old"),
    ("activation", 9), ("session_id", "other"), ("page_size", 2), ("target_found", 1),
    ("rows", [object()]), ("sources", [1]), ("locate_id", "unknown"), ("source", {}),
    ("kind", "controller"), ("contract", "foreign"),
])
def test_bad_page_is_protocol_damage_not_a_business_refusal(field, value):
    row = page()
    row[field] = value
    with pytest.raises(ProtocolError):
        WorkbenchPage(request(), row)


def test_query_publication_strips_source_preserves_page_and_rejects_oversize():
    history = History().populate(tasks=1, tools=3, reports=1)
    value = request()
    scope = SimpleNamespace(identity=cursor().identity, controller=None, committed=100,
                            validate_current=lambda: None)
    worker = WorkbenchService()
    try:
        source = worker.source(history.reader, scope=scope)
        _, published = source.query_pages(value.query).result(5)
        result = WorkbenchPage.from_publication(published, value, "data")
        restored = WorkbenchPage.from_record(roundtrip(result.record()), value)
        assert roundtrip(restored.record()) == roundtrip(result.record())
        assert restored.page["rows"][0]["id"] == published.pages["data"]["rows"][0]["id"]
        assert restored.page["total"] == published.pages["data"]["total"]
        assert not hasattr(restored, "source")
        with pytest.raises(TypeError):
            restored.page["rows"][0]["title"] = "changed"
        with pytest.raises(ProtocolError):
            json_view({"publication": published})
        with pytest.raises(ProtocolError):
            WorkbenchPage.from_publication(replace(published, identity=("other",)), value, "data")
        huge = page()
        huge["rows"][0]["title"] = "文" * 1500
        with pytest.raises(ProtocolError):
            WorkbenchPage(value, huge)
    finally:
        worker.close()
        assert worker.wait(5)
