"""Managed execution rejects malformed scope, delivery and lifecycle evidence."""

import base64
import queue
from copy import deepcopy
from types import SimpleNamespace

import pytest

from sico.codex.exec_events import ProcessEvents, notification
from sico.codex.exec_policy import sandbox_request
from sico.codex.exec_process import ManagedExecProcess
from sico.codex.rpc import RpcError


ACCESS = dict(sandbox="read-only", approval="never", network=False)


def fact(kind="output", sequence=1, **fields):
    values = dict(processId="process", seq=sequence)
    if kind == "output":
        values.update(stream="stdout", chunk=base64.b64encode(b"bytes\0\xff").decode())
    elif kind == "exited":
        values.update(exitCode=0, sandboxDenied=False)
    values.update(fields)
    return dict(method="process/" + kind, params=values)


def test_output_is_byte_exact_and_exit_is_not_output_closure():
    result = notification(fact(), "process", 1)
    assert result.data == b"bytes\0\xff" and result.kind == "output"
    assert notification(fact("exited"), "process", 1).kind == "exited"
    assert notification(fact("closed"), "process", 1).kind == "closed"


@pytest.mark.parametrize("values", [
    dict(processId="other"), dict(seq=0), dict(seq=True), dict(seq=2),
    dict(stream="pty"), dict(chunk="%%%"), dict(chunk=""), dict(extra="unknown"),
    dict(chunk=base64.b64encode(b"x" * 65537).decode()),
])
def test_invalid_output_identity_order_or_encoding_is_rejected(values):
    with pytest.raises(ValueError):
        notification(fact(**values), "process", 1)


@pytest.mark.parametrize("values", [dict(exitCode=True), dict(exitCode=None), dict(exitCode=-9),
                                   dict(exitCode=256), dict(sandboxDenied=1)])
def test_exit_does_not_invent_signal_or_success(values):
    with pytest.raises(ValueError):
        notification(fact("exited", **values), "process", 1)


def test_scope_is_detached_and_read_only_rejects_writable_roots(tmp_path):
    before = deepcopy(ACCESS)
    result = sandbox_request(ACCESS, tmp_path, home=tmp_path)
    assert ACCESS == before
    assert result["permissions"]["type"] == "managed"
    assert result["permissions"]["network"] == "restricted"
    assert len(result["permissions"]["file_system"]["entries"]) == 1
    with pytest.raises(ValueError, match="writable roots"):
        sandbox_request(ACCESS, tmp_path, home=tmp_path, writable_roots=[tmp_path])
    with pytest.raises(ValueError, match="managed"):
        sandbox_request(dict(ACCESS, sandbox="danger-full-access"), tmp_path, home=tmp_path)


@pytest.fixture
def server(monkeypatch, tmp_path):
    import sico.codex.exec_process as module

    class Rpc:
        def __init__(self, *args, **kwargs):
            self.requests, self.events, self.closes = [], [], []
            self.failure = None
            self.start_result = None
            assert kwargs["protocol"] == "exec-server"
            servers.append(self)

        def request(self, method, params, timeout=5):
            self.requests.append((method, deepcopy(params)))
            if self.failure == method:
                raise RpcError("uncertain delivery; no replay")
            if method == "environment/status":
                return dict(status="ready")
            if method == "process/start":
                return self.start_result or dict(processId=params["processId"], sandboxType="linuxSeccomp")
            if method == "process/write":
                return dict(status="accepted")
            if method == "process/terminate":
                return dict(running=True)
            pytest.fail("Unexpected RPC method: " + method)

        def next(self, _timeout):
            if not self.events:
                raise queue.Empty
            return self.events.pop(0)

        def close(self, *, require_confirmed):
            self.closes.append(require_confirmed)

    servers = []
    monkeypatch.setattr(module, "AppServer", Rpc)
    process = ManagedExecProcess("/fixed/codex", {}, cwd=tmp_path, home=tmp_path, access=ACCESS)
    yield SimpleNamespace(process=process, servers=servers)
    process.close()


def test_explicit_child_environment_and_single_creation(server):
    process = server.process
    child_environment = {"PATH": "/bin", "EMPTY": "", "LICENSE": "synthetic"}
    process.start(["/bin/bash"], child_environment)
    rpc = server.servers[0]
    params = rpc.requests[-1][1]
    assert params["env"] == child_environment
    assert params["envPolicy"]["inherit"] == "none"
    assert params["pipeStdin"] is True and params["tty"] is False
    with pytest.raises(RuntimeError, match="occupied"):
        process.start(["/bin/bash"], {})
    process.write(b"pwd\n")
    process.write(b"echo done\n")
    writes = [params for method, params in rpc.requests if method == "process/write"]
    assert [item["writeId"] for item in writes] == ["1", "2"]
    assert len({item["processId"] for item in writes}) == 1
    assert base64.b64decode(writes[0]["chunk"]) == b"pwd\n"


def test_uncertain_write_is_not_retried_and_cleanup_requires_confirmation(server):
    process = server.process
    process.start(["/bin/bash"], {})
    rpc = server.servers[0]
    rpc.failure = "process/write"
    with pytest.raises(RpcError, match="uncertain"):
        process.write(b"side_effect\n")
    with pytest.raises(RuntimeError):
        process.write(b"side_effect\n")
    assert len([x for x in rpc.requests if x[0] == "process/write"]) == 1
    assert rpc.closes == [True]


@pytest.mark.parametrize("later_event", [False, True])
def test_output_gap_closes_transport_without_publishing_completion(server, monkeypatch, later_event):
    process = server.process
    process.start(["/bin/bash"], {})
    rpc = server.servers[0]
    pid = rpc.requests[-1][1]["processId"]
    rpc.events.append(fact(sequence=2, processId=pid))
    monkeypatch.setattr("sico.codex.exec_process.time.monotonic", lambda: 10)
    assert process.read() is None
    if later_event:
        rpc.events.append(fact(sequence=3, processId=pid))
    monkeypatch.setattr("sico.codex.exec_process.time.monotonic", lambda: 12)
    with pytest.raises(RpcError, match="sequence"):
        process.read()
    assert rpc.closes == [True]


@pytest.mark.parametrize("kinds", [("closed",), ("exited", "exited")])
def test_invalid_lifecycle_cannot_publish_stream_closure(server, kinds):
    process = server.process
    process.start(["/bin/bash"], {})
    rpc = server.servers[0]
    pid = rpc.requests[-1][1]["processId"]
    rpc.events.extend(fact(kind, sequence=i + 1, processId=pid) for i, kind in enumerate(kinds))
    for _ in range(len(kinds) - 1):
        assert process.read().kind == "exited"
    with pytest.raises(ValueError, match="lifecycle"):
        process.read()
    assert rpc.closes == [True]


def test_buffered_output_after_closure_cannot_be_silently_lost():
    events = ProcessEvents("process")
    events.push(fact(sequence=2))
    events.push(fact("closed"))
    with pytest.raises(ValueError, match="beyond stream closure"):
        events.pop()


def test_concurrent_stream_reordering_publishes_only_contiguous_prefix():
    events = ProcessEvents("process")
    events.push(fact(sequence=2, stream="stderr"))
    events.push(fact("closed", sequence=4))
    assert events.waiting and events.pop() is None
    events.push(fact())
    assert events.pop().sequence == 1
    assert events.pop().sequence == 2
    assert events.pop() is None
    events.push(fact("exited", sequence=3))
    assert events.pop().kind == "exited"
    assert events.pop().kind == "closed"
    assert not events.waiting
    with pytest.raises(ValueError, match="Duplicate"):
        events.push(fact())


def test_unbounded_event_gap_is_rejected():
    events = ProcessEvents("process")
    with pytest.raises(ValueError, match="budget"):
        events.push(fact(sequence=129))


def test_terminate_receipt_does_not_mark_process_exited(server):
    process = server.process
    process.start(["/bin/bash"], {})
    rpc = server.servers[0]
    pid = rpc.requests[-1][1]["processId"]
    process.terminate()
    rpc.events.extend([fact(processId=pid), fact("exited", sequence=2, processId=pid),
                       fact("closed", sequence=3, processId=pid)])
    assert [process.read().kind for _ in range(3)] == ["output", "exited", "closed"]
    assert process.read() is None
    with pytest.raises(RuntimeError, match="exited"):
        process.write(b"pwd\n")


@pytest.mark.parametrize("method", ["read", "write"])
def test_process_waits_have_finite_caps_before_io(server, method):
    process = server.process
    with pytest.raises(ValueError):
        getattr(process, method)(*([b"data"] if method == "write" else []), timeout=30)
    assert not server.servers


def test_close_before_start_fences_late_creation(server):
    server.process.close()
    with pytest.raises(RuntimeError):
        server.process.start(["/bin/bash"], {})
    assert not server.servers


def test_lost_server_constructor_cannot_be_retired_as_confirmed(monkeypatch, tmp_path):
    def failed(*args, **kwargs):
        raise RuntimeError('supervisor cleanup is uncertain')
    monkeypatch.setattr('sico.codex.exec_process.AppServer', failed)
    process = ManagedExecProcess('/codex', {}, cwd=tmp_path, home=tmp_path, access=ACCESS)
    with pytest.raises(RuntimeError, match='reconciliation'):
        process.start(['/bin/bash'], {})
    with pytest.raises(RuntimeError, match='reconciliation'):
        process.close()
