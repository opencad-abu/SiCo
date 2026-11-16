"""Controller dispatch hooks keep call-time limits and single I/O ownership."""

from __future__ import annotations

from pathlib import Path

import pytest
from controller_transport_fixture import FakeTransport
from controller_transport_fixture import runtime as runtime

import cadai.controller as controller
from cadai.controller import RequestDispatcher
from cadai.socket_server import RequestFailure


@pytest.mark.parametrize(
    "method,hook,params",
    [
        ("eval_skill", "_eval_source", {"code": "nil"}),
        ("load_skill_file", "_materialize_skill_file", {"path": "probe.il"}),
        ("snapshot_schematic", "_snapshot_source", {"lib": "work", "cell": "top"}),
        ("capture_circuit_template", "_template_source", {}),
        ("inspect_config_binding", "_config_binding_source", {}),
        ("live_model_status", "_dispatch_live", {}),
    ],
)
def test_dispatch_uses_existing_request_hook(runtime, tmp_path, monkeypatch, method, hook, params):
    transport = FakeTransport()
    dispatcher = RequestDispatcher(transport, runtime, tmp_path)
    calls = []

    def stop(*args, **kwargs):
        calls.append((args, kwargs))
        raise RuntimeError("hook intercepted")

    monkeypatch.setattr(dispatcher, hook, stop)
    with pytest.raises(RuntimeError, match="hook intercepted"):
        dispatcher("hook-probe", method, params)
    assert calls == [(((method, params) if method.startswith("live_") else (params,)), {})]
    assert transport.requests == []
    assert dispatcher.runtime_cleanup_safe


def test_result_hooks_run_after_exactly_one_transport_request(runtime, tmp_path, monkeypatch):
    transport = FakeTransport()
    dispatcher = RequestDispatcher(transport, runtime, tmp_path)
    payload = {"ok": True, "value": "hook-result"}
    result = {"projected": True}
    names = []

    def read(name, *, wait_for_visibility):
        assert len(transport.requests) == 1
        assert wait_for_visibility is True
        names.append(name)
        return payload, b"raw"

    def bounded(value, name, raw):
        assert value is payload and name == names[0] and raw == b"raw"
        return result

    monkeypatch.setattr(dispatcher, "_result_payload", read)
    monkeypatch.setattr(dispatcher, "_bounded_result", bounded)
    assert dispatcher("result-hook", "get_context", {}) is result


def test_inline_limit_is_read_at_call_time(runtime, tmp_path, monkeypatch):
    dispatcher = RequestDispatcher(FakeTransport({"ok": True, "value": "small"}), runtime, tmp_path)
    monkeypatch.setattr(controller, "INLINE_RESULT_BYTES", 1)
    result = dispatcher("small-result", "get_context", {})
    assert result["spooled"] is True
    assert Path(result["artifact"]["path"]).parent == runtime.spool
    assert list(runtime.virtuoso_spool.iterdir()) == []


def test_artifact_limit_is_read_at_call_time(runtime, tmp_path, monkeypatch):
    dispatcher = RequestDispatcher(None, runtime, tmp_path)
    source = runtime.virtuoso_spool / "snapshot-data-limit.jsonl"
    source.write_bytes(b"{}\n")
    monkeypatch.setattr(controller, "SCHEMATIC_SNAPSHOT_MAX_BYTES", 1)
    with pytest.raises(RequestFailure, match="bounded regular file"):
        dispatcher._materialize_snapshot_artifact({"artifact_path": str(source)}, source)
    assert not source.exists()
    assert list(runtime.spool.iterdir()) == []


def test_hash_accessor_still_dispatches_to_old_binding_hook(monkeypatch):
    value = {"details": {}}
    seen = []

    def binding(received):
        seen.append(received)
        return {"source_generation": "hook-hash"}

    monkeypatch.setattr(controller, "_snapshot_binding_from_aivw", binding)
    assert controller._snapshot_generation_from_aivw(value) == "hook-hash"
    assert seen == [value]
