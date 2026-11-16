"""Failure-path contracts for the MCP resource owner and bounded responses."""

import io
import json
from types import SimpleNamespace

import pytest

from cadai import mcp
from cadai.candidate_ledger import CandidateLedgerError
from cadai.schematic_snapshot import SnapshotError


def call(server, name, arguments=None):
    return server.handle({"id": 1, "method": "tools/call", "params": {
        "name": name, "arguments": arguments or {},
    }})


def detail(response):
    return json.loads(response["result"]["content"][0]["text"])


@pytest.mark.parametrize("fails", ["pdk", "snapshot", "reference", "bridge"])
def test_shutdown_attempts_all_resources_after_any_close_failure(fails):
    closed = []

    class Resource:
        def __init__(self, name):
            self.name = name

        def close(self):
            closed.append(self.name)
            if self.name == fails:
                raise OSError(self.name)

    server = mcp.McpServer(Resource("bridge"), reader=io.BytesIO(),
                           skill_reference=Resource("reference"),
                           snapshot_store=Resource("snapshot"))
    server._pdk_session = Resource("pdk")
    with pytest.raises(OSError, match=fails):
        server.run()
    assert closed == ["pdk", "snapshot", "reference", "bridge"]


def test_snapshot_unavailable_and_constructor_failure_keep_tool_error(monkeypatch, tmp_path):
    unavailable = call(mcp.McpServer(object()), "snapshot_schematic")
    unavailable_detail = detail(unavailable)
    assert unavailable_detail["code"] == "snapshot_error"
    assert unavailable_detail["cause"] == "schematic snapshot storage is unavailable"
    assert unavailable_detail["message"].startswith(
        "schematic snapshot storage is unavailable"
    )
    assert unavailable_detail["tool"] == "snapshot_schematic"
    assert unavailable_detail["retry_same_request"] is False

    def fail(_):
        raise OSError("index unavailable")

    monkeypatch.setattr(mcp, "SchematicSnapshotStore", fail)
    server = mcp.McpServer(SimpleNamespace(runtime=SimpleNamespace(spool=tmp_path)))
    failure = detail(call(server, "snapshot_schematic"))
    assert failure["cause"] == "index unavailable"
    assert failure["message"].startswith("index unavailable")
    assert server.snapshot_store is None


def test_failed_snapshot_keeps_initialized_store_owned_for_cleanup(monkeypatch, tmp_path):
    created, closed = [], []

    class Store:
        def __init__(self, spool):
            created.append(self)

        def query(self, *args, **kwargs):
            raise SnapshotError("query failed")

        def close(self):
            closed.append(self)

    monkeypatch.setattr(mcp, "SchematicSnapshotStore", Store)
    server = mcp.McpServer(SimpleNamespace(
        runtime=SimpleNamespace(spool=tmp_path), close=lambda: None,
    ))
    for _ in range(2):
        failed = detail(call(
            server, "query_schematic", {"snapshot_id": "snap_" + "a" * 64}
        ))
        assert failed["cause"] == "query failed"
        assert failed["message"].startswith("query failed")
    assert len(created) == 1 and server.snapshot_store is created[0]
    server.close()
    assert closed == created


def test_failed_candidate_retains_same_ledger_for_get_and_feedback(monkeypatch, tmp_path):
    from cadai import mcp_aivw_handler
    created, used = [], []

    class Ledger:
        def __init__(self, spool):
            created.append(self)

        def submit(self, arguments):
            used.append(self)
            raise CandidateLedgerError("invalid candidate")

        def get(self, candidate, *, include_source):
            used.append(self)
            return {"candidate": candidate}

        def attach_feedback(self, candidate, feedback):
            used.append(self)
            return {"attached": candidate}

    monkeypatch.setattr(mcp, "CandidateLedger", Ledger)
    monkeypatch.setattr(mcp_aivw_handler, "run_aivw_recipe", lambda *a, **k: {"ok": True})
    server = mcp.McpServer(
        SimpleNamespace(runtime=SimpleNamespace(spool=tmp_path)),
        profile="verification",
    )
    assert call(server, "submit_candidate", {
        "source": "module top; endmodule",
        "module": "top",
        "language": "systemverilog",
        "source_generation": "a" * 64,
    })["error"]["code"] == -32602
    assert detail(call(
        server, "get_candidate", {"candidate_sha256": "a" * 64}
    ))["candidate"] == "a" * 64
    assert detail(call(server, "run_aivw_recipe", {
        "reference": "fixture", "through": "snapshot", "candidate_sha256": "a" * 64,
    }))["candidate_feedback"] == {"attached": "a" * 64}
    assert len(created) == 1 and used == [created[0]] * 3


@pytest.mark.parametrize("payload,message", [
    ({1: "bad"}, "keys must be text"),
    ({"nested": {"api_key": "secret"}}, "credential field"),
    ({"nested": {"provider_verdict": "PASS"}}, "verdict fields"),
    ({"number": float("nan")}, "non-finite"),
    ({"events": [None] * 129}, "list is too large"),
])
def test_live_invalid_response_has_stable_protocol_error(payload, message):
    server = mcp.McpServer(SimpleNamespace(call=lambda *args: (True, payload)))
    response = call(server, "live_model_status")
    assert response["error"]["code"] == -32602
    assert message in response["error"]["message"]


@pytest.mark.parametrize("name,arguments", [
    ("live_model_status", {"extra": True}),
    ("live_model_events", {"limit": True}),
    ("live_model_events", {"extra": True}),
    ("live_model_start", {"recipe_id": "missing required fields"}),
])
def test_live_invalid_request_never_reaches_bridge(name, arguments):
    server = mcp.McpServer(SimpleNamespace(call=lambda *args: pytest.fail("sent invalid request")))
    assert call(server, name, arguments)["error"]["code"] == -32602


def test_template_preview_uses_retained_bindings_without_eager_initialization(monkeypatch):
    from circuit_spec_fixture import sample

    from cadai import mcp_bindings
    seen = []
    monkeypatch.setattr(mcp_bindings, "dispatch_template", lambda name, args, **kwargs: (
        seen.append(kwargs["pdk_bindings"]) or True, {},
    ))
    spec, bindings = sample()
    arguments = {"spec": spec, "bindings": bindings}
    server = mcp.McpServer(object())
    call(server, "preview_circuit_spec", arguments)
    assert server._pdk_session is None and seen == [None]
    retained = object()
    server._pdk_bindings = retained
    call(server, "preview_circuit_spec", arguments)
    assert seen == [None, retained]


def test_pdk_constructor_failure_is_not_retried_as_a_legacy_constructor(monkeypatch, tmp_path):
    calls = []

    def entry():
        return {"ok": True}

    client = SimpleNamespace(pdk_entry_context=entry)

    def constructor(value, **kwargs):
        calls.append((value, kwargs))
        raise TypeError("inner operation got an unexpected keyword argument 'captured'")

    monkeypatch.setattr(mcp, 'PdkSession', constructor)
    server = mcp.McpServer(client, workspace=tmp_path)
    with pytest.raises(TypeError, match='captured'):
        server.pdk_session()
    assert calls == [(client, {'workspace': tmp_path, 'entry': entry})]
    assert server._pdk_session is None
