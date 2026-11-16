"""F2 durable admission, receipts and recovery never replay SKILL operations."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from test_agent_bridge import bound, connect_peer, reply

from sico.core.contracts import NeedsReconcile
from sico.transport.bridge_client import BridgeClient
from sico.transport.instance_bridge import InstanceBridge
from sico.transport.methods import QueryUnavailable


def test_relay_reconnect_preserves_pending_id_and_recovers_complete_late_result(tmp_path):
    from io import StringIO

    from sico.transport.relay import StdioLines, relay

    read_fd, write_fd = os.pipe()
    source = os.fdopen(read_fd, "rb", buffering=0)
    reader = StdioLines(source)
    sink = StringIO()
    with InstanceBridge(tmp_path, bound(), operation_timeout=1) as bridge, ThreadPoolExecutor(1) as pool:
        thread = threading.Thread(target=relay, args=(*bridge.broker.address, bridge.broker.token),
                                  kwargs=dict(reader=reader, sink=sink, registration={"context": bound().record()},
                                              targets=bridge.targets, router=bridge.router, skill_timeout=1), daemon=True)
        thread.start()
        try:
            with BridgeClient.discover(tmp_path, bound()) as client:
                bridge.broker.context(bound().instance_id)
                client.register_session("owner", bound())
                pending = pool.submit(client.get_context, bound())
                deadline = time.monotonic() + 3
                while not sink.getvalue():
                    assert time.monotonic() < deadline
                    time.sleep(.01)
                request_id = sink.getvalue().split()[0]
                old = bridge.broker.peers[bound().instance_id]
                old.connection.close()
                with pytest.raises(NeedsReconcile):
                    pending.result(3)
                deadline = time.monotonic() + 4
                while bridge.broker.peers.get(bound().instance_id) in {None, old}:
                    assert time.monotonic() < deadline
                    time.sleep(.01)
                with pytest.raises(NeedsReconcile, match="skill_blocked_unknown"):
                    client.get_context(bound())
                os.write(write_fd, (json.dumps(dict(id="f" * 32, ok=True, result={"value": "foreign"})) + "\n").encode())
                os.write(write_fd, (json.dumps(dict(id=request_id, ok=True,
                                                   result={"valid": True, "value": "late-after-reconnect"})) + "\n").encode())
                deadline = time.monotonic() + 3
                while client.router_snapshot(bound())["unknown"]:
                    assert time.monotonic() < deadline
                    time.sleep(.01)
                receipt = client.request_receipt(bound(), request_id, session_id="owner")
                assert receipt["state"] == "late_reply"
                assert receipt["reply"]["result"]["value"] == "late-after-reconnect"
                assert len(sink.getvalue().splitlines()) == 1
                followup = pool.submit(client.get_context, bound())
                deadline = time.monotonic() + 3
                while len(sink.getvalue().splitlines()) < 2:
                    assert time.monotonic() < deadline
                    time.sleep(.01)
                next_id = sink.getvalue().splitlines()[1].split()[0]
                os.write(write_fd, (json.dumps(dict(id=request_id, ok=True, result={"value": "duplicate"})) + "\n").encode())
                os.write(write_fd, (json.dumps(dict(id=next_id, ok=True, result={"valid": True, "value": "next"})) + "\n").encode())
                assert followup.result(3)["value"] == "next"
                assert client.request_receipt(bound(), request_id, session_id="owner")["reply"]["result"]["value"] == "late-after-reconnect"
        finally:
            reader.close()
            os.close(write_fd)
            thread.join(3)
            source.close()
            assert not thread.is_alive()


def test_admission_and_dispatch_are_durable_before_skill_and_receipt_survives_exit(tmp_path):
    with InstanceBridge(tmp_path, bound()) as bridge, ThreadPoolExecutor(1) as pool:
        peer = connect_peer(bridge.broker, bound())
        with BridgeClient.discover(tmp_path, bound()) as client:
            client.register_session("owner", bound())
            pending = pool.submit(client.get_context, bound())
            request = peer.receive()
            receipt = client.request_receipt(bound(), request["id"], session_id="owner")
            assert receipt["state"] == "running"
            assert receipt["session_id"] == "owner"
            assert receipt["queue_deadline"] >= receipt["accepted_at"]
            assert receipt["execution_deadline"] > receipt["started_at"]
            archived = BridgeClient.archived_receipt(tmp_path, bound(), request["id"], session_id="owner")
            assert archived["state"] == "running" and not archived.get("recovered")
            with pytest.raises(NeedsReconcile, match="owner"):
                client.request_receipt(bound(), request["id"], session_id="another")
            reply(peer, request, "persisted")
            assert pending.result(3)["value"] == "persisted"
        peer.close()
    receipt = BridgeClient.archived_receipt(tmp_path, bound(), request["id"], session_id="owner")
    assert receipt["state"] == "completed"
    assert receipt["reply"]["result"]["value"] == "persisted"
    assert receipt["automatic_resume_allowed"] is False
    entry = Path(__file__).absolute().parents[3] / "sico/python/sico-entry"
    response = subprocess.run([sys.executable, str(entry), "router-receipt", "--launch-dir",
                               str(tmp_path), "--instance", bound().instance_id, "--generation", "g",
                               "--session", "owner", "--request", request["id"]],
                              capture_output=True, text=True, check=True, timeout=5)
    assert json.loads(response.stdout) == receipt


def test_router_crash_recovers_status_and_results_without_restoring_executable_queue(tmp_path):
    from sico.transport.router_journal import RouterJournal

    code = """
import os, sys
from sico.transport.router_journal import RouterJournal
j = RouterJournal(sys.argv[1], instance_id='i', generation='g', bridge_id='b', router_id='r')
j.record(dict(request_id='a'*32, session_id='s', target_id='t', state='accepted', accepted_at=1))
j.record(dict(request_id='a'*32, session_id='s', target_id='t', state='running', started_at=2))
j.record(dict(request_id='b'*32, session_id='s', target_id='t', state='queued', accepted_at=3))
os._exit(0)
"""
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path))
    subprocess.run([sys.executable, "-c", code, str(tmp_path / "journal")], env=env, check=True)
    with RouterJournal(tmp_path / "journal", instance_id="i", generation="g", bridge_id="b",
                       router_id="r") as journal:
        running = journal.receipt("a" * 32, session_id="s", target_id="t")
        queued = journal.receipt("b" * 32, session_id="s", target_id="t")
        assert running["state"] == "timed_out_unknown"
        assert queued["state"] == "cancelled_before_start"
        assert running["automatic_resume_allowed"] is False
        assert queued["automatic_resume_allowed"] is False
        assert running["recovered"] and queued["recovered"]


def test_persistence_failure_never_dispatches_skill(tmp_path, monkeypatch):
    from sico.transport.router import SkillInstanceRouter
    from sico.transport.router_journal import RouterJournal

    with RouterJournal(tmp_path, instance_id="i", generation="g", bridge_id="b", router_id="r") as journal:
        router = SkillInstanceRouter("i", "g", journal=journal)
        calls = []

        def fail(_record):
            raise OSError("disk full")
        monkeypatch.setattr(journal, "record", fail)
        with pytest.raises(QueryUnavailable, match="persist"):
            router.execute("read", lambda request: calls.append(request), wait=True)
        assert calls == []
        assert router.snapshot()["closed"]


def test_late_receipt_resolves_only_original_session_and_is_readable_after_release(tmp_path):
    with InstanceBridge(tmp_path, bound()) as bridge, ThreadPoolExecutor(1) as pool:
        peer = connect_peer(bridge.broker, bound())
        with BridgeClient.discover(tmp_path, bound()) as first, BridgeClient.discover(tmp_path, bound()) as second:
            first.register_session("first", bound())
            second.register_session("second", bound())
            pending = pool.submit(first.get_context, bound())
            request = peer.receive()
            peer.send(dict(protocol="cad_ai_transport.v1", kind="response", generation="g",
                           id=request["id"], ok=False, code="skill_timeout"))
            with pytest.raises(NeedsReconcile):
                pending.result(3)
            first.release_session("first")
            with pytest.raises(NeedsReconcile, match="skill_blocked_unknown"):
                second.get_context(bound())
            peer.send(dict(protocol="cad_ai_transport.v1", kind="skill_late_reply", generation="g",
                           id=request["id"], ok=True,
                           reply=dict(id=request["id"], ok=True, result={"valid": True, "value": "late"})))
            import time
            deadline = time.monotonic() + 3
            while second.router_snapshot(bound())["unknown"]:
                assert time.monotonic() < deadline
                time.sleep(.01)
            receipt = first.request_receipt(bound(), request["id"], session_id="first")
            assert receipt["state"] == "late_reply"
            assert receipt["reply"]["result"]["value"] == "late"
            assert receipt["session_id"] == "first"
            assert receipt["caller_state"] == "timed_out_unknown"
            assert receipt["automatic_resume_allowed"] is False
        peer.close()


def test_journal_recovers_only_partial_tail_and_rejects_identity_corruption(tmp_path):
    from sico.transport.router_journal import RouterJournal

    identity = dict(instance_id="i", generation="g", bridge_id="b", router_id="r")
    with RouterJournal(tmp_path, **identity) as journal:
        journal.record(dict(request_id="a" * 32, session_id="s", target_id="t", state="completed"))
    path = tmp_path / "requests.jsonl"
    valid = path.read_bytes()
    with path.open("ab") as stream:
        stream.write(b'{"unfinished":')
    with RouterJournal(tmp_path, **identity) as journal:
        assert journal.receipt("a" * 32, session_id="s", target_id="t")["state"] == "completed"
    assert path.read_bytes() == valid
    with pytest.raises(ValueError, match="identity"):
        RouterJournal(tmp_path, **dict(identity, generation="different"))
    assert path.read_bytes() == valid
    damaged = json.loads(valid)
    damaged["record"]["state"] = "failed"
    path.write_text(json.dumps(damaged) + "\n")
    with pytest.raises(ValueError, match="checksum"):
        RouterJournal(tmp_path, **identity)


def test_failed_running_state_persistence_never_dispatches(tmp_path, monkeypatch):
    from sico.transport.router import SkillInstanceRouter
    from sico.transport.router_journal import RouterJournal

    with RouterJournal(tmp_path, instance_id="i", generation="g", bridge_id="b", router_id="r") as journal:
        router = SkillInstanceRouter("i", "g", journal=journal)
        original = journal.record
        calls = []
        def record(row):
            if row["state"] == "running":
                raise OSError("disk unavailable")
            original(row)
        monkeypatch.setattr(journal, "record", record)
        with pytest.raises(QueryUnavailable):
            router.execute("read", lambda request: calls.append(request), wait=True)
        assert not calls and router.snapshot()["closed"]
