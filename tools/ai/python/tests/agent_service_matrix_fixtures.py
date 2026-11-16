"""Spawned service fault points and protocol-only clients for AS-07B."""

import multiprocessing
import os
import socket
import time
from contextlib import contextmanager
from types import SimpleNamespace

from agent_service_launch_fixtures import eventually

from sico.service.remote_frontend import RemoteFrontend
from sico.service.service_discovery import discover_project
from sico.transport.framing import Connection


def _service(project, channel, entered, release, evidence, pause_catalog):
    from sico.core.contracts import ModelTurn, TextDelta
    from sico.service import project_sessions
    from sico.service.service_entry import run_service

    if pause_catalog:
        from sico.storage.history import CatalogIndex

        scan = CatalogIndex.scan
        def paused_scan(index, progress=None):
            def partial(rows):
                if progress:
                    progress(rows)
                if not entered.is_set():
                    entered.set()
                    assert release.wait(40), "Catalog barrier expired"
            return scan(index, partial)
        CatalogIndex.scan = paused_scan

    class Provider:
        label = "AS07 controlled provider"
        base = ""

        def __init__(self, config, environment):
            self.model = (config or {}).get("model", "matrix")
            self.path = environment.get("PATH", "")

        def stream(self, system, messages, tools, cancelled):
            prompt = messages[-1]["content"]
            evidence.put(dict(pid=os.getpid(), prompt=prompt, model=self.model, path=self.path))
            if prompt.startswith("hold"):
                entered.set()
                if not release.wait(40):
                    raise TimeoutError("AS07 service barrier expired")
            yield TextDelta("completed " + self.model)
            yield ModelTurn("completed " + self.model)

    project_sessions.provider_from_config = lambda config, *, environment: Provider(
        config, environment)
    raise SystemExit(run_service(project, bootstrap_fd=channel.detach()))


@contextmanager
def matrix_service(project, *, pause_catalog=False):
    project.mkdir(parents=True, exist_ok=True)
    context = multiprocessing.get_context("spawn")
    entered, release, evidence = context.Event(), context.Event(), context.Queue()
    parent, child = socket.socketpair()
    process = context.Process(target=_service,
        args=(project, child, entered, release, evidence, pause_catalog))
    process.start()
    child.close()
    try:
        assert Connection(parent).receive(deadline=time.monotonic() + 10)["kind"] == "ready"
        descriptor = discover_project(project).service
        assert descriptor.pid == process.pid
        yield SimpleNamespace(process=process, descriptor=descriptor, entered=entered,
                              release=release, evidence=evidence)
    finally:
        # A killed waiter may own the Event lock; never touch its synchronization objects.
        if process.is_alive():
            release.set()
            process.terminate()
        process.join(6)
        if process.is_alive():
            process.kill()
            process.join(5)
            raise AssertionError("Matrix service outlived fixture")
        parent.close()
        evidence.close()
        evidence.join_thread()


def client(stack, service, project, session="live", *, create=True, model="matrix"):
    api = RemoteFrontend(service.descriptor, project, session)
    def close():
        api.close_desktop()
        eventually(lambda: api.wait_desktop(0), 5)
    stack.callback(close)
    params = dict(session_id=session)
    if create:
        params.update(provider_config=dict(provider="openai", model=model,
                                           base_url="https://example.invalid/v1"),
                      environment={"PATH": model, "SICO_API_KEY": "memory-only-matrix"},
                      timeout=3, context_file=None)
    frontend = api.open_initial("demo" if create else "attach", params).result(8)
    return api, frontend.session


def completed(api, token, key):
    return api.query_input(token, key).result(5).status.execution == "completed"


def events(api, prepared):
    from agent_service_gui_fixtures import drain

    return [event for batch in drain(api, prepared.stream) for event in batch.events]


def admitting_client(project, channel):
    """Client process holds no execution owner and can be killed after durable admission."""
    api = RemoteFrontend(discover_project(project).service, project, "live")
    try:
        frontend = api.open_initial("demo", dict(session_id="live", provider_config=None,
            environment={}, timeout=3, context_file=None)).result(8)
        token = frontend.session
        api.control.request(token, "acquire").result(5)
        keys = [api.command(token, "submit", prompt).result(5)
                for prompt in ("hold after client crash", "queued before client crash")]
        channel.send((token, keys))
        channel.recv()
    finally:
        api.close_desktop()
        channel.close()


def _host(project, context, channel, stopped):
    from sico.demo import DemoPeer
    from sico.transport.instance_bridge import InstanceBridge

    with InstanceBridge(project, context) as bridge:
        peer = DemoPeer(bridge.broker, context)
        try:
            channel.send(bridge.descriptor)
            stopped.wait(60)
        finally:
            peer.close()
            channel.close()


@contextmanager
def simulated_host(project, context):
    """Real bridge/relay processes with explicitly simulated Virtuoso data."""
    mp = multiprocessing.get_context("spawn")
    parent, child = mp.Pipe(duplex=False)
    stopped = mp.Event()
    process = mp.Process(target=_host, args=(project, context, child, stopped))
    process.start()
    child.close()
    try:
        assert parent.poll(10)
        yield SimpleNamespace(process=process, descriptor=parent.recv(), context=context)
    finally:
        if process.is_alive():
            stopped.set()
        process.join(5)
        if process.is_alive():
            process.kill()
            process.join(5)
            raise AssertionError("Simulated host outlived fixture")
        parent.close()
