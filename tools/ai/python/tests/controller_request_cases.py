"""Controller request materialization, results and transport failure contracts."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

import pytest

import cadai.controller as controller_module
from cadai.controller import RequestDispatcher
from cadai.protocol import ProtocolError
from cadai.runtime import RuntimePaths, write_spool
from cadai.socket_server import RequestFailure
from cadai.transport import TransportClosed
from controller_transport_fixture import FakeTransport


def test_eval_materializes_controller_owned_source(runtime: RuntimePaths, tmp_path: Path) -> None:
    transport = FakeTransport({"ok": True, "value": "3", "output": "电源"})
    dispatcher = RequestDispatcher(transport, runtime, tmp_path)
    result = dispatcher("a" * 32, "eval_skill", {"code": "1+2"})
    request = transport.requests[0]
    assert transport.sources == [b"1+2"]
    assert Path(request["source_path"]).parent == runtime.virtuoso_spool
    assert Path(request["output_path"]).parent == runtime.virtuoso_spool
    assert not Path(request["source_path"]).exists()
    assert result == {"ok": True, "value": "3", "output": "电源"}


def test_all_virtuoso_request_paths_use_shared_spool_while_socket_stays_local(
    runtime: RuntimePaths, tmp_path: Path
) -> None:
    class SharedOnlyTransport(FakeTransport):
        def request(self, payload: dict[str, Any], timeout: float):
            for key in ("source_path", "output_path"):
                if raw_path := payload.get(key):
                    path = Path(raw_path)
                    assert path.parent == runtime.virtuoso_spool
                    assert runtime.root not in path.parents
            return super().request(payload, timeout)

    source = tmp_path / "shared-path-probe.ils"
    source.write_text("nil\n", encoding="ascii")
    transport = SharedOnlyTransport()
    dispatcher = RequestDispatcher(transport, runtime, tmp_path)

    dispatcher("1" * 32, "eval_skill", {"code": "1+2"})
    dispatcher("2" * 32, "load_skill_file", {"path": source.name})
    dispatcher("3" * 32, "get_context", {})

    assert runtime.socket.parent == runtime.root
    assert runtime.spool.parent == runtime.root
    assert runtime.virtuoso_spool != runtime.spool
    assert [request["method"] for request in transport.requests] == [
        "eval_skill",
        "load_skill_file",
        "get_context",
    ]
    assert all(
        Path(request["output_path"]).parent == runtime.virtuoso_spool
        for request in transport.requests
    )
    assert all(
        Path(request["source_path"]).parent == runtime.virtuoso_spool
        for request in transport.requests[:2]
    )
    assert list(runtime.virtuoso_spool.iterdir()) == []
    assert list(runtime.spool.iterdir()) == []


def test_eval_consumes_inbound_and_controller_spools(runtime: RuntimePaths, tmp_path: Path) -> None:
    source = b"x" * 40_000
    inbound = write_spool(runtime.spool, "mcp-source", source)
    dispatcher = RequestDispatcher(FakeTransport(), runtime, tmp_path)
    dispatcher(
        "9" * 32,
        "eval_skill",
        {
            "source_spool": {
                "name": inbound["name"],
                "size": inbound["size"],
                "sha256": inbound["sha256"],
            }
        },
    )
    assert not (runtime.spool / str(inbound["name"])).exists()
    assert list(runtime.spool.iterdir()) == []


@pytest.mark.parametrize("suffix", [".il", ".ils"])
def test_relative_load_is_resolved_from_workspace(
    runtime: RuntimePaths, tmp_path: Path, suffix: str
) -> None:
    source = tmp_path / f"my skill{suffix}"
    source.write_text("x=1\n", encoding="utf-8")
    transport = FakeTransport()
    dispatcher = RequestDispatcher(transport, runtime, tmp_path)
    dispatcher("b" * 32, "load_skill_file", {"path": source.name})
    materialized = Path(transport.requests[0]["source_path"])
    assert materialized != source.resolve()
    assert materialized.suffix == suffix
    assert transport.sources == [b"x=1\n"]
    assert not materialized.exists()


def test_relative_load_cannot_escape_workspace(runtime: RuntimePaths, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.il"
    outside.write_text("nil\n", encoding="utf-8")
    dispatcher = RequestDispatcher(FakeTransport(), runtime, workspace)
    with pytest.raises(RequestFailure, match="inside the workspace"):
        dispatcher("e" * 32, "load_skill_file", {"path": "../outside.il"})


def test_skill_fifo_is_rejected_without_waiting_for_a_writer(
    runtime: RuntimePaths, tmp_path: Path
) -> None:
    fifo = tmp_path / "blocked.il"
    os.mkfifo(fifo)
    dispatcher = RequestDispatcher(FakeTransport(), runtime, tmp_path)
    with pytest.raises(RequestFailure, match="regular file"):
        dispatcher("6" * 32, "load_skill_file", {"path": fifo.name})


def test_timeout_is_sent_once_and_never_retried(runtime: RuntimePaths, tmp_path: Path) -> None:
    transport = FakeTransport(timeout=True)
    dispatcher = RequestDispatcher(transport, runtime, tmp_path, timeout=0.01)
    with pytest.raises(RequestFailure, match="not retried") as caught:
        dispatcher("c" * 32, "eval_skill", {"code": "sleep(10)"})
    assert caught.value.code == "timeout"
    assert len(transport.requests) == 1
    assert dispatcher.runtime_cleanup_safe is False
    source_path = Path(transport.requests[0]["source_path"])
    assert source_path.parent == runtime.virtuoso_spool
    assert source_path.is_file()


def test_transport_disconnect_preserves_runtime(runtime: RuntimePaths, tmp_path: Path) -> None:
    class ClosedTransport(FakeTransport):
        def request(self, payload: dict[str, Any], timeout: float):
            self.requests.append(dict(payload))
            raise TransportClosed("Virtuoso exited")

    dispatcher = RequestDispatcher(ClosedTransport(), runtime, tmp_path)
    with pytest.raises(RequestFailure, match="not retried") as caught:
        dispatcher("f" * 32, "eval_skill", {"code": "sleep(10)"})
    assert caught.value.code == "bridge_closed"
    assert dispatcher.runtime_cleanup_safe is False


def test_malformed_transport_response_preserves_runtime(
    runtime: RuntimePaths, tmp_path: Path
) -> None:
    class MalformedTransport(FakeTransport):
        def request(self, payload: dict[str, Any], timeout: float):
            self.requests.append(dict(payload))
            raise ProtocolError("invalid_response", "response ok must be boolean")

    transport = MalformedTransport()
    dispatcher = RequestDispatcher(transport, runtime, tmp_path)
    with pytest.raises(RequestFailure, match="not retried") as caught:
        dispatcher("7" * 32, "eval_skill", {"code": "dangerousCall()"})
    assert caught.value.code == "bridge_closed"
    assert dispatcher.runtime_cleanup_safe is False
    assert Path(transport.requests[0]["source_path"]).is_file()


def test_non_object_result_is_removed_from_shared_bridge(
    runtime: RuntimePaths, tmp_path: Path
) -> None:
    transport = FakeTransport()
    transport.result = ["not-an-object"]
    dispatcher = RequestDispatcher(transport, runtime, tmp_path)

    with pytest.raises(RequestFailure, match="must be a JSON object") as caught:
        dispatcher("8" * 32, "eval_skill", {"code": "nil"})

    assert caught.value.code == "invalid_result"
    assert list(runtime.virtuoso_spool.iterdir()) == []


def test_large_result_returns_spool_reference(runtime: RuntimePaths, tmp_path: Path) -> None:
    transport = FakeTransport({"ok": True, "value": "nil", "output": "x" * 70_000})
    dispatcher = RequestDispatcher(transport, runtime, tmp_path)
    result = dispatcher("d" * 32, "eval_skill", {"code": "nil"})
    assert result["spooled"] is True
    artifact = Path(result["artifact"]["path"])
    assert artifact.parent == runtime.spool
    assert runtime.root in artifact.parents
    assert list(runtime.virtuoso_spool.iterdir()) == []
    raw = artifact.read_bytes()
    assert result["artifact"]["size"] == len(raw)
    assert result["artifact"]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert len(result["preview"]) == 8_192


def test_large_result_local_copy_failure_cleans_shared_bridge(
    runtime: RuntimePaths, tmp_path: Path, monkeypatch
) -> None:
    transport = FakeTransport({"ok": True, "value": "nil", "output": "x" * 70_000})
    dispatcher = RequestDispatcher(transport, runtime, tmp_path)
    real_write_spool = controller_module.write_spool

    def fail_local_result(spool_dir, prefix, data, *, suffix=""):
        if spool_dir == runtime.spool and prefix == "result":
            raise OSError("local spool is unavailable")
        return real_write_spool(spool_dir, prefix, data, suffix=suffix)

    monkeypatch.setattr(controller_module, "write_spool", fail_local_result)
    with pytest.raises(OSError, match="local spool is unavailable"):
        dispatcher("0" * 32, "eval_skill", {"code": "nil"})

    assert list(runtime.virtuoso_spool.iterdir()) == []
    assert list(runtime.spool.iterdir()) == []
