"""Isolated bursts use the real host wire, durable inbox and serial controller."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pytest
from agent_service_business_fixtures import Provider, business_service
from agent_service_launch_fixtures import eventually
from sico.core.ask_queue import QueueFull
from sico.service.frontend_ask import FrontendAsk
from sico.service.host_contract import host_identity
from sico.service.inbox import SessionInbox
from sico.storage.host_routes import HostRoutes
from sico.storage.inbox_records import read_input
from sico.storage.journal import SessionJournal
from sico.transport.bridge_client import BridgeClient
from test_agent_inbox import context, message
from test_agent_service_host import host_bridge, query, target


def ask(root, captured, sequence):
    with closing(FrontendAsk(root, captured, environment={})) as frontend:
        return frontend.submit(captured.target_id, captured.target_id, ask_sequence=sequence)


def sequenced(key, sequence):
    return dict(message(key), ask_sequence=sequence)


def test_twenty_pending_accept_first_sixteen_sort_and_explicitly_refuse_rest(tmp_path):
    with SessionJournal(tmp_path, "burst") as journal:
        inbox = SessionInbox(journal, context())
        for sequence in range(1, 21):
            item = sequenced("q" + str(sequence), sequence)
            if sequence <= 16:
                assert inbox.accept(item)
            else:
                with pytest.raises(QueueFull):
                    inbox.accept(item)
        assert [r["message"]["ask_sequence"] for r in inbox.pending] == list(range(1, 17))
        assert len(list(inbox.directory.glob("*.json"))) == 16
        # A full queue still returns the original acceptance for the original ID.
        assert not inbox.accept(sequenced("q1", 1))
        for sequence in range(1, 17):
            assert inbox.take()[2] == "q" + str(sequence)
            inbox.finish(dict(id="task" + str(sequence), status="completed"))


def test_active_is_never_preempted_and_restart_keeps_sequence_without_replay(tmp_path):
    with SessionJournal(tmp_path, "late") as journal:
        inbox = SessionInbox(journal, context())
        assert inbox.accept(sequenced("active", 9))
        assert inbox.take()[2] == "active"
        for key, seq in (("later", 3), ("earlier", 1), ("middle", 2)):
            inbox.accept(sequenced(key, seq))
        assert inbox.take() is None and inbox.active["id"] == "active"
        assert [r["id"] for r in inbox.pending] == ["earlier", "middle", "later"]
        restarted = SessionInbox(journal, context())
        assert not restarted.pending and restarted.take() is None
        assert read_input(restarted.directory, "middle")["message"]["ask_sequence"] == 2
        assert read_input(restarted.directory, "active")["started_at"] > 0
        assert not restarted.accept(sequenced("middle", 2))
        assert not restarted.pending
        with pytest.raises(ValueError, match="sequence"):
            restarted.accept(sequenced("different_id", 2))
        inbox.finish(dict(id="task", status="completed"))
        assert inbox.take()[2] == "earlier"


def test_legacy_arrival_order_is_preserved_after_explicit_sequences(tmp_path):
    with SessionJournal(tmp_path, "mixed") as journal:
        inbox = SessionInbox(journal, context())
        for item in (message("old1"), sequenced("new2", 2), message("old2"),
                     sequenced("new1", 1)):
            inbox.accept(item)
        assert [r["id"] for r in inbox.pending] == ["new1", "new2", "old1", "old2"]


def test_three_views_reverse_arrival_burst_and_durable_overflow(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    provider = Provider(entered, release)
    with host_bridge(tmp_path, target(tmp_path)) as (_, client):
        with business_service(tmp_path, monkeypatch, provider=provider,
                              bridge_factory=BridgeClient) as service:
            first = ask(tmp_path, target(tmp_path, "active"), 1)
            assert entered.wait(3)
            owner = service.owner.controllers[first.address.session.session_id]
            for sequence in range(17, 1, -1):
                view = ("schematic", "maestro", "layout")[sequence % 3]
                receipt = ask(tmp_path, target(tmp_path, "q" + str(sequence), view=view), sequence)
                assert receipt.address == first.address and receipt.ask_sequence == sequence
                assert receipt.queue_head == dict(input_id="active", ask_sequence=1)
            before = [r["id"] for r in owner.inbox.pending]
            for sequence in range(18, 22):
                receipt = ask(tmp_path, target(tmp_path, "q" + str(sequence)), sequence)
                assert (receipt.outcome, receipt.code, receipt.input_id) == (
                    "rejected", "queue_full", "q" + str(sequence))
                assert query(service, client, receipt.input_id) == receipt
            assert [r["id"] for r in owner.inbox.pending] == before
            assert provider.seen == ["active"] and len(service.owner.controllers) == 1
            release.set()
            assert owner.wait(8)
            assert provider.seen == ["active"] + ["q" + str(n) for n in range(2, 18)]
            assert ask(tmp_path, target(tmp_path, "q18"), 18).code == "queue_full"
            assert ask(tmp_path, target(tmp_path, "q2", view="layout"), 2).outcome == "accepted"
            duplicate = ask(tmp_path, target(tmp_path, "duplicate_sequence"), 2)
            wrong_sequence = ask(tmp_path, target(tmp_path, "q2", view="layout"), 22)
            assert duplicate.code == wrong_sequence.code == "operation_conflict"
            assert len(provider.seen) == 17
            assert {r["id"]: r["ask_sequence"] for r in owner.history.snapshot()} == dict(
                active=1, **{"q" + str(n): n for n in range(2, 18)})
        with business_service(tmp_path, monkeypatch, bridge_factory=BridgeClient) as restarted:
            receipt = query(restarted, client, "q18")
            assert receipt.outcome == "rejected" and receipt.code == "queue_full"
            assert query(restarted, client, "q2").ask_sequence == 2
            assert not restarted.owner.controllers and not restarted.provider.seen


def test_different_cells_execute_independently_while_same_cell_waits(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    with host_bridge(tmp_path, target(tmp_path)), business_service(
            tmp_path, monkeypatch, provider=Provider(entered, release),
            bridge_factory=BridgeClient) as service:
        a = ask(tmp_path, target(tmp_path, "first"), 1)
        assert entered.wait(3)
        assert ask(tmp_path, target(tmp_path, "second", view="layout"), 2).address == a.address
        b = ask(tmp_path, target(tmp_path, "other", cell="Other"), 3)
        eventually(lambda: len(service.provider.seen) == 2)
        assert a.address != b.address and service.provider.seen == ["first", "other"]
        release.set()
        for owner in service.owner.controllers.values():
            assert owner.wait(5)
        assert service.provider.seen == ["first", "other", "second"]


def test_service_capacity_refusal_is_durable_and_does_not_fetch_or_create(tmp_path, monkeypatch):
    held, release = threading.Event(), threading.Event()
    with host_bridge(tmp_path, target(tmp_path)) as (_, client):
        with business_service(tmp_path, monkeypatch, capacity=1,
                              bridge_factory=BridgeClient) as service:
            original = service.dispatch._business.host.execute

            def hold(request):
                if request["operation_id"] == "first":
                    held.set()
                    assert release.wait(5)
                return original(request)

            monkeypatch.setattr(service.dispatch._business.host, "execute", hold)
            with ThreadPoolExecutor(max_workers=2) as workers:
                first = workers.submit(ask, tmp_path, target(tmp_path, "first"), 1)
                assert held.wait(3)
                second = workers.submit(ask, tmp_path, target(tmp_path, "refused"), 2)
                try:
                    eventually(lambda: service.dispatch._refusals == 1)
                    assert not second.done()
                finally:
                    release.set()
                assert first.result(5).outcome == "accepted"
                receipt = second.result(5)
            assert receipt.code == "queue_full" and receipt.input_id == "refused"
            row = HostRoutes(tmp_path, service.descriptor.project_id).read(
                host_identity(client.descriptor), "refused")
            assert row["ask_sequence"] == 2 and row["received_at"] > 0
            assert row["address"] is None and row["rejection"] == receipt.record()
            assert query(service, client, "refused") == receipt
            owner, = service.owner.controllers.values()
            assert owner.wait(5) and service.provider.seen == ["first"]
        with business_service(tmp_path, monkeypatch, bridge_factory=BridgeClient) as restarted:
            assert query(restarted, client, "refused") == receipt
            assert ask(tmp_path, target(tmp_path, "refused"), 2) == receipt
            assert not restarted.owner.controllers and not restarted.provider.seen


@pytest.mark.parametrize("sequence", [0, -1, True, 1.0, "1", 2147483648])
def test_invalid_sequence_never_reaches_discovery(tmp_path, monkeypatch, sequence):
    monkeypatch.setattr("sico.service.host_gateway.ServiceStartup",
                        lambda *_a, **_k: pytest.fail("invalid sequence reached discovery"))
    with pytest.raises(ValueError):
        ask(tmp_path, target(tmp_path, "bad"), sequence)


def test_sequence_corruption_fails_closed(tmp_path):
    from sico.storage.host_sequences import claim_sequence
    from sico.storage.project_files import registry_directory
    from sico.transport.framing import ProtocolError

    host = dict(instance_id="i", generation="g", bridge_id="b", router_id="r")
    assert claim_sequence(tmp_path, host, 1, "original")
    assert claim_sequence(tmp_path, host, 1, "original")
    assert not claim_sequence(tmp_path, host, 1, "other")
    path, = (registry_directory(tmp_path) / "host-sequences").glob("*.json")
    row = json.loads(path.read_text())
    row["input_id"] = "invalid/id"
    path.write_text(json.dumps(row))
    with pytest.raises(ProtocolError):
        claim_sequence(tmp_path, host, 1, "other")
