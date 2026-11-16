"""Multi-project/session/client isolation, crash recovery and reconnect over real processes."""

import multiprocessing
from contextlib import ExitStack

import pytest
from agent_service_launch_fixtures import eventually
from agent_service_matrix_fixtures import (
    admitting_client,
    client,
    completed,
    events,
    matrix_service,
)

from sico.service.service_messages import ServiceRequest
from sico.transport.framing import ProtocolError


def test_projects_and_sessions_keep_execution_configuration_and_control_isolated(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    with matrix_service(a) as first, matrix_service(b) as second, ExitStack() as stack:
        assert first.process.pid != second.process.pid
        assert first.descriptor.project_id != second.descriptor.project_id
        one, t1 = client(stack, first, a, model="alpha")
        sibling, ts = client(stack, first, a, "sibling", model="beta")
        other, t2 = client(stack, second, b, model="gamma")
        observer, to = client(stack, first, a, create=False)
        assert to == t1 and not observer.can_control(to)
        for api, token in ((one, t1), (sibling, ts), (other, t2)):
            api.control.request(token, "acquire").result(5)
        with pytest.raises(ValueError):
            observer.command(to, "submit", "observer denied").result(5)
        active = one.command(t1, "submit", "hold alpha").result(5)
        assert first.entered.wait(5)
        queued = one.command(t1, "submit", "queued alpha").result(5)
        for api, token in ((sibling, ts), (other, t2)):
            key = api.command(token, "submit", "independent").result(5)
            eventually(lambda: completed(api, token, key))
        assert not first.release.is_set()
        records = [first.evidence.get(timeout=3) for _ in range(2)]
        assert {(r["model"], r["path"]) for r in records} == {("alpha", "alpha"), ("beta", "beta")}
        assert second.evidence.get(timeout=3)["model"] == "gamma"
        # Even a well-formed request carrying another project's address is rejected.
        with pytest.raises(ProtocolError):
            other.recovery.input_result(one.address(t1).record(), active).result(5)
        assert one.service_status().result(5).status.pending.sessions >= 1
        one.close_desktop()
        assert first.process.is_alive() and second.process.is_alive()
        assert observer.query_input(to, queued).result(5).status.execution == "queued"
        first.release.set()
        eventually(lambda: completed(observer, to, active) and completed(observer, to, queued))
        rows = events(observer, observer.prepare_replay(to, 1, 200).result(5))
        assert len([r for r in rows if r["kind"] == "task.started"]) == 2
        assert first.evidence.get(timeout=3)["prompt"] == "queued alpha"


def test_observer_disconnect_replays_exact_sequence_without_affecting_other_reader(tmp_path):
    with matrix_service(tmp_path) as service, ExitStack() as stack:
        owner, token = client(stack, service, tmp_path)
        owner.control.request(token, "acquire").result(5)
        slow, slow_token = client(stack, service, tmp_path, create=False)
        fast, fast_token = client(stack, service, tmp_path, create=False)
        slow_feed = slow.prepare_replay(slow_token, 1, 200).result(5)
        fast_feed = fast.prepare_replay(fast_token, 1, 200).result(5)
        before = events(slow, slow_feed)
        key = owner.command(token, "submit", "hold for reconnect").result(5)
        assert service.entered.wait(5)
        first = events(fast, fast_feed)
        assert any(row["kind"] == "task.started" for row in first)
        # Drop the actual socket and reconnect the same captured replay window.
        slow._events.retire_connection()
        eventually(lambda: not slow._events.retiring.is_set())
        slow.reconnect_session(slow_token, slow_feed.stream)
        restored = slow.prepare_replay(slow_token, 2, 200).result(5)
        service.release.set()
        eventually(lambda: completed(owner, token, key))
        from agent_service_gui_fixtures import drain

        batches = drain(slow, restored.stream)
        recovered = [row for batch in batches for row in batch.events]
        fast_rows = first + events(fast, fast_feed)
        assert all(b.start == a.end + 1 for a, b in zip(batches, batches[1:]))
        assert batches[-1].end == batches[-1].committed
        assert len({row["sequence"] for row in recovered}) == len(recovered)
        assert recovered[:len(before)] == before
        assert recovered == fast_rows
        assert sum(row["kind"] == "task.completed" for row in recovered) == 1
        assert service.process.is_alive() and not slow.can_control(slow_token)


def test_killed_project_leaves_sibling_live_and_restart_does_not_replay_unknown_inputs(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    with matrix_service(a) as first, matrix_service(b) as second, ExitStack() as stack:
        old, token = client(stack, first, a)
        healthy, ht = client(stack, second, b)
        old.control.request(token, "acquire").result(5)
        healthy.control.request(ht, "acquire").result(5)
        running = old.command(token, "submit", "hold before crash").result(5)
        assert first.entered.wait(5)
        queued = old.command(token, "submit", "never replay automatically").result(5)
        address = old.address(token).record()
        first.process.kill()
        first.process.join(5)
        assert not first.process.is_alive()
        good = healthy.command(ht, "submit", "survive other project").result(5)
        eventually(lambda: completed(healthy, ht, good))
        with matrix_service(a) as restarted, ExitStack() as restored_stack:
            api, _ = client(restored_stack, restarted, a, "fresh")
            assert restarted.descriptor.project_id == first.descriptor.project_id
            assert restarted.descriptor.service_id != first.descriptor.service_id
            view = api.recovery.inspect("live").result(5)
            assert {row["input_id"] for row in view["inputs"]} == {running, queued}
            recovered = api.recovery.restore(view, "recover-live",
                {"SICO_API_KEY": "memory-only-matrix"}).result(8)
            current = api.open_session("live").result(5)
            assert current.runtime_id != token.runtime_id and not api.can_control(current)
            rows = events(api, api.prepare_replay(current, 1, 200).result(5))
            assert sum(row["kind"] == "task.started" for row in rows) == 1
            assert recovered["task_status"] == "needs_reconcile"
            original = api.recovery.input_result(address, running).result(5).status
            assert original.outcome == "accepted"
            assert api.recovery.input_result(address, queued).result(5).status.execution == "queued"
            api.control.request(current, "acquire").result(5)
            with pytest.raises(ValueError):
                api.command(current, "resume", expected_task="").result(5)
            assert api._admin.request(ServiceRequest()).result(5).status.pending.reconcile
            # Inspection of the same operation is stable and creates no second runtime.
            assert api.recovery.inspect("live", "recover-live").result(5)["operation"]
            assert api.open_session("live").result(5) == current


def test_killed_admitting_client_keeps_queue_and_requires_explicit_takeover(tmp_path):
    with matrix_service(tmp_path) as service, ExitStack() as stack:
        mp = multiprocessing.get_context("spawn")
        parent, child = mp.Pipe()
        writer = mp.Process(target=admitting_client, args=(tmp_path, child))
        writer.start()
        child.close()
        try:
            assert parent.poll(10)
            original, keys = parent.recv()
            assert service.entered.wait(5)
            observer, token = client(stack, service, tmp_path, create=False)
            assert token == original
            assert observer.control.request(token).result(5).state == "occupied"
            writer.kill()
            writer.join(5)
            eventually(lambda: observer.control.request(token).result(5).state == "detached")
            assert service.process.is_alive() and not service.release.is_set()
            assert not observer.can_control(token)
            with pytest.raises(ValueError):
                observer.control.request(token, "acquire").result(5)
            assert observer.query_input(token, keys[1]).result(5).status.execution == "queued"
            observer.control.request(token, "takeover").result(5)
            assert observer.can_control(token)
            service.release.set()
            eventually(lambda: all(completed(observer, token, key) for key in keys))
            rows = events(observer, observer.prepare_replay(token, 1, 200).result(5))
            assert sum(row["kind"] == "task.started" for row in rows) == 2
            assert [service.evidence.get(timeout=3)["prompt"] for _ in range(2)] == [
                "hold after client crash", "queued before client crash"]
        finally:
            if writer.is_alive():
                writer.kill()
            writer.join(5)
            parent.close()
