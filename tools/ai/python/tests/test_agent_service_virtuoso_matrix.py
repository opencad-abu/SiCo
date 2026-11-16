"""Two real Virtuoso hosts share a service and isolate captured-host process loss."""

import json
import os
from contextlib import ExitStack
from pathlib import Path

import pytest
from agent_gui_support import isolated_display
from agent_service_gui_fixtures import drain
from agent_service_launch_fixtures import eventually
from agent_service_lifecycle_fixtures import service_process
from agent_service_virtuoso_fixtures import fingerprint, read_site, virtuoso_host
from test_agent_service_host_matrix import open_host

pytestmark = pytest.mark.skipif(not os.environ.get("SICO_AS07_SITE"),
                               reason="AS07 real Virtuoso requires SICO_AS07_SITE")


def test_two_readonly_virtuosos_keep_sessions_isolated_after_one_host_exits(tmp_path):
    site, runtime, design = read_site(os.environ["SICO_AS07_SITE"])
    before = fingerprint(design)
    evidence = Path(os.environ.get("SICO_AS07_EVIDENCE", str(tmp_path)))
    evidence.mkdir(mode=0o700, parents=True, exist_ok=True)
    project = tmp_path / "project"
    project.mkdir()
    with isolated_display(evidence) as environment, ExitStack() as hosts:
        first = hosts.enter_context(virtuoso_host(project, evidence / "host-a", site,
                                                  runtime, environment["DISPLAY"]))
        second = hosts.enter_context(virtuoso_host(project, evidence / "host-b", site,
                                                   runtime, environment["DISPLAY"]))
        with service_process(project) as (process, descriptor), ExitStack() as clients:
            from types import SimpleNamespace

            service = SimpleNamespace(descriptor=descriptor)
            a, at = open_host(clients, service, project, first, "a")
            b, bt = open_host(clients, service, project, second, "b")
            assert first.context.instance_id != second.context.instance_id
            assert len({first.process.pid, second.process.pid, process.pid}) == 3
            def execute(api, token, text):
                key = api.command(token, "submit", text).result(8)
                eventually(lambda: api.query_input(token, key).result(5).status.execution
                           == "completed", 20)
                return key
            execute(a, at, "Read captured context of host A")
            execute(b, bt, "Read captured context of host B")
            feed = a.prepare_replay(at, 1, 200).result(5)
            rows = [row for batch in drain(a, feed.stream) for row in batch.events]
            first.process.kill()
            first.process.wait(5)
            def lost():
                rows.extend(row for batch in drain(a, feed.stream) for row in batch.events)
                return any(row["kind"] == "session.target_unavailable" for row in rows)
            eventually(lost, 15)
            assert a.view(at).context == first.context
            with pytest.raises(ValueError):
                a.command(at, "submit", "must not move to host B").result(5)
            execute(b, bt, "Read surviving host again")
            assert process.poll() is None and second.process.poll() is None
            observed = b.prepare_replay(bt, 1, 200).result(5)
            healthy = [row for batch in drain(b, observed.stream) for row in batch.events]
            finished = [row for row in healthy if row["kind"] == "tool.finished"]
            assert len(finished) == 2
            for row in finished:
                assert row["payload"]["result"]["status"] == "ok"
                assert row["payload"]["result"]["data"]["cellview"] == site["target"]
            report = dict(service_pid=process.pid,
                host_pids=[first.process.pid, second.process.pid],
                versions=[first.version, second.version], target=site["target"],
                context_sha256=runtime["context_sha256"], surviving_tool_results=len(finished))
            (evidence / "result.json").write_text(json.dumps(report, indent=2) + '\n')
    assert fingerprint(design) == before
