"""Explicit end reaps actual Codex/MCP children while the service stays ready."""

import json
import os
import signal
import sys
from contextlib import ExitStack
from types import SimpleNamespace

import pytest
from agent_service_business_fixtures import exchange, open_request, query_request
from agent_service_launch_fixtures import eventually
from agent_service_lifecycle_fixtures import service_process
from sico.demo import DemoPeer
from sico.service.service_messages import ServiceRequest
from sico.storage.journal import SessionJournal
from sico.transport.instance_bridge import InstanceBridge
from test_agent_codex_native import answer, gateway, native_codex  # noqa: F401
from test_agent_service_control import attach
from test_agent_service_execution import SOURCE, descendants

from cadai.process_monitor import identity


def observe_processes(children):
    result = {}
    for pid in children:
        try:
            result[pid] = identity(pid)
        except FileNotFoundError:
            pass
    return result


def same_live_process(expected):
    try:
        current = identity(expected["pid"])
        return current["start"] == expected["start"] and current["os_state"] not in {"Z", "X"}
    except FileNotFoundError:
        return False


def cleanup_test_descendants(captured, directory):
    # Failure containment only: the production assertion covers every captured
    # descendant before this callback runs, including native plugin discovery.
    remaining = [row for row in captured.values() if same_live_process(row)]
    (directory / "remaining-test-descendants.json").write_text(json.dumps(remaining))
    for row in remaining:
        try:
            if same_live_process(row):
                os.kill(row["pid"], signal.SIGTERM)
        except ProcessLookupError:
            pass
    for row in remaining:
        try:
            if same_live_process(row):
                os.kill(row["pid"], signal.SIGKILL)
        except ProcessLookupError:
            pass


@pytest.mark.parametrize("shutdown", ["end", "force", "frozen_force"])
def test_shutdown_reaps_native_codex_mcp_and_preserves_evidence(
        tmp_path, native_codex, gateway, monkeypatch, shutdown):  # noqa: F811
    gateway.responses.put(answer("complete before ending"))
    config = dict(backend="codex", provider="responses", model="service-test", tool_format="flat",
                  base_url=f"http://127.0.0.1:{gateway.server_port}/v1",
                  codex_executable=native_codex, timeout=10)
    environment = {"PATH": "/usr/bin:/bin:/software/pkgs/nodejs/node-v20.16.0-linux-x64/bin",
                   "HOME": str(tmp_path), "SICO_PYTHON": sys.executable,
                   "SICO_API_KEY": "as05b-test-secret", "NO_PROXY": "127.0.0.1,localhost",
                   "no_proxy": "127.0.0.1,localhost"}
    with InstanceBridge(tmp_path, SOURCE) as bridge, ExitStack() as stack:
        peer = DemoPeer(bridge.broker, SOURCE)
        stack.callback(peer.close)
        with service_process(tmp_path) as (process, descriptor):
            service = SimpleNamespace(descriptor=descriptor)
            opened = exchange(service, open_request(context=SOURCE, bridge=bridge.descriptor,
                              config=config, environment=environment))
            assert opened.outcome == "opened"
            with ExitStack() as clients:
                api, token = attach(clients, service, tmp_path)
                api.control.request(token, "acquire").result(5)
                operation = api.command(token, "submit", "say complete").result(5)
                eventually(lambda: exchange(service, query_request(opened.address, operation))
                           .execution == "completed", 15)
                children = descendants(process.pid)
                captured = observe_processes(children)
                clients.callback(cleanup_test_descendants, captured, tmp_path)
                assert any(b"app-server" in command for command in children.values())
                assert any(b"sico.codex.mcp" in command for command in children.values())
                if shutdown == "end":
                    api.end_session(token, "native-end").result(5)
                    eventually(lambda: api.end_session(token, "native-end", observe=True)
                               .result(5).state == "ended", 15)
                else:
                    if shutdown == "frozen_force":
                        import sico.service.service_force_stop as stopping

                        monkeypatch.setattr(stopping, "GRACE_SECONDS", .15)
                        os.kill(process.pid, signal.SIGSTOP)
                    assert api.force_stop_service().result(11).service_id == descriptor.service_id
                    process.wait(timeout=2)
                def children_gone():
                    return not any(same_live_process(row) for row in captured.values())
                try:
                    eventually(children_gone, 5)
                except AssertionError:
                    raise AssertionError(dict(captured=children,
                                              remaining=descendants(process.pid))) from None
                if shutdown == "end":
                    assert process.poll() is None
                    assert exchange(service, ServiceRequest()).pending.sessions == 0
                with SessionJournal(tmp_path, "live") as journal:
                    if shutdown == "end":
                        assert journal.events()[-1]["payload"]["state"] == "drained"
                    assert "as05b-test-secret" not in str(journal.events())
