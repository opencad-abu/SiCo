"""Real service/client processes and service-owned native Codex/MCP lifetime."""

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

from agent_service_business_fixtures import exchange, open_request, query_request, submit_request
from agent_service_launch_fixtures import eventually
from agent_service_lifecycle_fixtures import service_process
from test_agent_codex_native import answer, gateway, native_codex  # noqa: F401

from sico.core.contracts import BoundContext
from sico.demo import DemoPeer
from sico.service.service_messages import ServiceRequest
from sico.service.service_session_dto import SessionAddress
from sico.storage.journal import SessionJournal
from sico.transport.instance_bridge import InstanceBridge

SOURCE = BoundContext("native", "generation", "target", {"source": "simulated", "valid": True})


def descendants(pid):
    found = {}
    root = Path("/proc") / str(pid)
    try:
        children = {int(child) for task in (root / "task").iterdir()
                    for child in (task / "children").read_text().split()}
    except FileNotFoundError:
        return found
    for child in children:
        try:
            found[child] = (Path("/proc") / str(child) / "cmdline").read_bytes()
            found.update(descendants(child))
        except FileNotFoundError:
            pass
    return found


def test_client_process_exit_keeps_codex_mcp_and_durable_queue_running(
        tmp_path, native_codex, gateway):  # noqa: F811
    entered, release = threading.Event(), threading.Event()

    def paused(_request):
        entered.set()
        assert release.wait(20)
        return answer("first complete")

    gateway.responses.put(paused)
    gateway.responses.put(answer("second complete"))
    environment = {"PATH": "/usr/bin:/bin:/software/pkgs/nodejs/node-v20.16.0-linux-x64/bin",
                   "HOME": str(tmp_path),
                   "SICO_PYTHON": sys.executable, "SICO_API_KEY": "as03b-native-secret",
                   "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"}
    config = dict(backend="codex", provider="responses", model="service-test", tool_format="flat",
                  base_url=f"http://127.0.0.1:{gateway.server_port}/v1",
                  codex_executable=native_codex, timeout=10)
    with InstanceBridge(tmp_path, SOURCE) as bridge:
        peer = DemoPeer(bridge.broker, SOURCE)
        try:
            with service_process(tmp_path) as (process, descriptor):
                service = SimpleNamespace(descriptor=descriptor)
                # A real independent client opens and submits; its process then exits.
                code = '''
import json, sys, time
from sico.service.service_discovery import discover_project
from dataclasses import asdict
from sico.service.service_channel import service_channel
from sico.service.service_messages import ServiceRequest
p = json.load(sys.stdin)
manager = service_channel(discover_project(sys.argv[1]).service, deadline=time.monotonic()+10)
client = manager.__enter__()
opened = client.exchange(ServiceRequest('session.open', params=p),
                         deadline=time.monotonic()+10).status
assert opened.outcome == 'opened', opened
address = opened.address.record()
lease = client.exchange(ServiceRequest('session.control', params=dict(address=address,
    action='acquire', generation=0, previous=None)), deadline=time.monotonic()+5).status
for key in ('first', 'second'):
    request = ServiceRequest('session.submit', key,
        dict(address=address, context=p['context'], text='Please say ' + key),
        control=asdict(lease.claim))
    assert client.exchange(request, deadline=time.monotonic()+10).status.outcome == 'accepted'
manager.__exit__(None, None, None)
print(json.dumps(address), flush=True)
'''
                child = subprocess.Popen([sys.executable, "-c", code, str(tmp_path)],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, env=dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path)))
                try:
                    params = open_request(
                        context=SOURCE, bridge=bridge.descriptor, config=config,
                        environment={"PATH": environment["PATH"], "HOME": environment["HOME"],
                                     "SICO_PYTHON": environment["SICO_PYTHON"],
                                     "SICO_API_KEY": environment["SICO_API_KEY"],
                                     "NO_PROXY": environment["NO_PROXY"],
                                     "no_proxy": environment["no_proxy"]}).params
                    stdout, stderr = child.communicate(json.dumps(params), timeout=15)
                    assert child.returncode == 0, stderr
                    address = SessionAddress.from_record(json.loads(stdout))
                    assert entered.wait(5)
                    assert process.poll() is None and not release.is_set()
                    children = descendants(process.pid)
                    assert any(b"app-server" in command for command in children.values())
                    assert any(b"sico.codex.mcp" in command for command in children.values())
                    for pid, command in children.items():
                        if b"sico.codex.mcp" in command:
                            raw = (Path("/proc") / str(pid) / "environ").read_bytes()
                            assert b"as03b-native-secret" not in raw
                    assert exchange(service, query_request(address, "second")).execution == "queued"
                    assert exchange(service, ServiceRequest()).pending.sessions == 1
                    release.set()
                    for key in ("first", "second"):
                        eventually(lambda: exchange(service, query_request(address, key)).execution
                                   == "completed", 15)
                    assert len(gateway.requests) == 2
                    assert exchange(service, submit_request(address, "first", "Please say first",
                                                            SOURCE)).outcome == "accepted"
                    assert len(gateway.requests) == 2
                finally:
                    release.set()
                    if child.poll() is None:
                        child.kill()
                        child.communicate(timeout=3)
                def process_gone():
                    for pid in children:
                        stat_path = Path("/proc") / str(pid) / "stat"
                        command_path = Path("/proc") / str(pid) / "cmdline"
                        if not stat_path.exists():
                            continue
                        try:
                            command = command_path.read_bytes()
                            state = stat_path.read_text().split()[2]
                        except FileNotFoundError:
                            continue
                        if b"app-server" not in command and b"sico.codex.mcp" not in command:
                            continue
                        if state not in {"Z", "X"}:
                            return False
                    return True

            eventually(process_gone, 5)
            # Service cleanup releases its writer, without the client owning that resource.
            with SessionJournal(tmp_path, "live") as journal:
                assert len([r for r in journal.events() if r["kind"] == "task.started"]) == 2
                assert "as03b-native-secret" not in str(journal.events())
        finally:
            release.set()
            peer.close()
