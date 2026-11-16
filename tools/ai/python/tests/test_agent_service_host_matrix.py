"""Independent simulated hosts exercise captured-origin loss and replacement isolation."""

from contextlib import ExitStack

import pytest
from agent_service_launch_fixtures import eventually
from agent_service_matrix_fixtures import completed, events, matrix_service, simulated_host
from test_agent_service_host import target

from sico.service.remote_frontend import RemoteFrontend


def open_host(stack, service, project, host, session):
    api = RemoteFrontend(service.descriptor, project, session)
    stack.callback(api.close_desktop)
    frontend = api.open_initial("open", dict(session_id=session, bridge=host.descriptor,
        context=host.context, provider_config=None, environment={})).result(8)
    api.control.request(frontend.session, "acquire").result(5)
    return api, frontend.session


def test_host_kill_keeps_captured_target_and_queued_input_while_sibling_completes(tmp_path):
    first = target(tmp_path, instance="host-a")
    second = target(tmp_path, instance="host-b", cell="Other")
    with simulated_host(tmp_path, first) as a, simulated_host(tmp_path, second) as b, (
            matrix_service(tmp_path)) as service, ExitStack() as stack:
        assert len({a.process.pid, b.process.pid, service.process.pid}) == 3
        api, token = open_host(stack, service, tmp_path, a, "a")
        sibling, st = open_host(stack, service, tmp_path, b, "b")
        active = api.command(token, "submit", "hold original host").result(5)
        assert service.entered.wait(5)
        queued = api.command(token, "submit", "retain original target").result(5)
        prepared = api.prepare_replay(token, 1, 200).result(5)
        rows = events(api, prepared)
        a.process.kill()
        a.process.join(5)
        def unavailable():
            rows.extend(events(api, prepared))
            return any(row["kind"] == "session.target_unavailable" for row in rows)
        eventually(unavailable, 10)
        assert api.view(token).context == first
        healthy = sibling.command(st, "submit", "healthy other host").result(5)
        eventually(lambda: completed(sibling, st, healthy))
        assert b.process.is_alive() and service.process.is_alive()
        service.release.set()
        eventually(lambda: api.query_input(token, active).result(5).status.execution
                   == "needs_reconcile")
        assert api.query_input(token, queued).result(5).status.execution == "queued"
        replacement = target(tmp_path, instance="host-a", generation="replacement")
        with simulated_host(tmp_path, replacement) as new:
            fresh, ft = open_host(stack, service, tmp_path, new, "replacement")
            key = fresh.command(ft, "submit", "new explicit target").result(5)
            eventually(lambda: completed(fresh, ft, key))
            assert fresh.view(ft).context == replacement
            assert api.view(token).context == first
            with pytest.raises(ValueError):
                api.command(token, "submit", "never rebind old queue").result(5)
        seen = [service.evidence.get(timeout=3)["prompt"] for _ in range(3)]
        assert seen == ["hold original host", "healthy other host", "new explicit target"]
        rows.extend(events(api, prepared))
        assert sum(row["kind"] == "task.started" for row in rows) == 1
