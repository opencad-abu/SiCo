"""Wire session/cursor values reject live resources and retired identity generations."""

from concurrent.futures import Future
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest
from agent_query_harness import EventFeed
from agent_service_dto_fixtures import cursor, roundtrip

from sico.service.frontend_session import SessionToken
from sico.service.replay_view import CursorPosition, ReplayView
from sico.service.service_session_dto import ReplayCursor, SessionAddress
from sico.service.service_values import json_view
from sico.transport.framing import ProtocolError


def test_cursor_roundtrip_contains_only_values_and_reuses_session_token():
    value = cursor()
    restored = ReplayCursor.from_record(roundtrip(value.record()))
    assert restored == value and type(restored.address.session) is SessionToken
    assert restored.address.session == SessionToken("query", "c" * 32)
    with pytest.raises(FrozenInstanceError):
        restored.sequence = 3


@pytest.mark.parametrize("field,value", [
    ("project_id", "wrong"), ("service_id", None), ("runtime_id", "legacy-owner"),
    ("session_id", "../session"), ("runtime_id", True), ("_owner", "controller"),
])
def test_session_address_rejects_invalid_or_extra_fields(field, value):
    row = cursor().address.record()
    row[field] = value
    with pytest.raises(ProtocolError):
        SessionAddress.from_record(row)


@pytest.mark.parametrize("field,value", [
    ("sequence", True), ("sequence", -1), ("sequence", 101), ("committed", 2**63),
    ("state_version", -2), ("activation", True), ("activation", -1),
    ("subscription_id", "old"), ("connection_id", ""), ("instance_id", "../i"),
    ("generation", {}), ("readonly", 1), ("stream", {}),
])
def test_cursor_schema_and_watermark_are_strict(field, value):
    row = cursor().record()
    row[field] = value
    with pytest.raises(ProtocolError):
        ReplayCursor.from_record(row)


def test_closed_history_has_no_live_runtime_and_cannot_gain_a_moving_watermark():
    address = replace(cursor().address, session=SessionToken("query", None))
    value = cursor(address=address, readonly=True)
    assert ReplayCursor.from_record(roundtrip(value.record())) == value
    with pytest.raises(ProtocolError):
        replace(value, readonly=False)
    with pytest.raises(ProtocolError):
        replace(value, committed=101).require_successor(value)
    replace(value, sequence=30).require_successor(value)


@pytest.mark.parametrize("change", ["project", "service", "session", "runtime", "activation",
                                   "connection", "subscription", "target", "generation",
                                   "regression", "committed", "version", "readonly"])
def test_cursor_cannot_drift_to_another_owner_or_retired_page(change):
    value = cursor(sequence=10, state_version=2)
    options = {
        "project": dict(address=replace(value.address, project_id="f" * 32)),
        "service": dict(address=replace(value.address, service_id="f" * 32)),
        "session": dict(address=replace(value.address, session=SessionToken("other", "c" * 32))),
        "runtime": dict(address=replace(value.address, session=SessionToken("query", "f" * 32))),
        "activation": dict(activation=8), "connection": dict(connection_id="f" * 32),
        "subscription": dict(subscription_id="f" * 32), "target": dict(instance_id="other"),
        "generation": dict(generation="g2"), "regression": dict(sequence=9),
        "committed": dict(committed=99), "version": dict(state_version=1),
        "readonly": dict(readonly=True),
    }
    with pytest.raises(ProtocolError):
        replace(value, **options[change]).require_successor(value)
    replace(value, sequence=20, committed=101, state_version=3).require_successor(value)


@pytest.mark.parametrize("resource", [Future(), object(), Path("/tmp/source"), CursorPosition(1),
                                     EventFeed.__new__(EventFeed), ReplayView(None, 1)])
def test_json_boundary_never_stringifies_local_resources(resource):
    with pytest.raises(ProtocolError, match="Only JSON"):
        json_view({"resource": resource})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 2**63, "\ud800", "文" * 1500,
                                  [None] * 2049, {"k" * 97: 1}])
def test_json_value_budgets_include_utf8_bytes_and_numeric_ranges(value):
    with pytest.raises(ProtocolError):
        json_view({"data": value})


def test_nested_and_circular_values_fail_without_unbounded_recursion():
    circular = {}
    circular["self"] = circular
    with pytest.raises(ProtocolError, match="nesting"):
        json_view(circular)


def test_published_json_is_detached_and_deeply_immutable():
    source = {"rows": [{"title": "first"}]}
    published = json_view(source)
    source["rows"][0]["title"] = "changed"
    assert published["rows"][0]["title"] == "first"
    with pytest.raises(TypeError):
        published["rows"][0]["title"] = "write"
