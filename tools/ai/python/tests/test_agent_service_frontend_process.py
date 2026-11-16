"""Independent GUI clients reconnect to work that remains owned by the service process."""

import json
import os
import subprocess
import sys
import threading

from agent_service_gui_fixtures import drain
from agent_service_launch_fixtures import eventually
from agent_service_lifecycle_fixtures import service_process
from test_agent_codex_native import answer, gateway, native_codex  # noqa: F401

from sico.service.remote_frontend import RemoteFrontend


def test_remote_client_exit_and_second_client_replay_native_queue(
        tmp_path, native_codex, gateway):  # noqa: F811
    entered, release = threading.Event(), threading.Event()

    def paused(_request):
        entered.set()
        assert release.wait(20)
        return answer("first complete")

    gateway.responses.put(paused)
    gateway.responses.put(answer("second complete"))
    config = dict(backend="codex", provider="responses", model="service-test", tool_format="flat",
                  base_url=f"http://127.0.0.1:{gateway.server_port}/v1",
                  codex_executable=native_codex, timeout=10)
    environment = dict(PATH="/usr/bin:/bin:/software/pkgs/nodejs/node-v20.16.0-linux-x64/bin",
                       HOME=str(tmp_path), SICO_PYTHON=sys.executable, SICO_API_KEY="private-test",
                       NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost")
    with service_process(tmp_path) as (process, descriptor):
        code = '''
import json, sys
from sico.service.remote_frontend import RemoteFrontend
from sico.service.service_discovery import discover_project
p = json.load(sys.stdin)
api = RemoteFrontend(discover_project(sys.argv[1]).service, sys.argv[1], 'live')
try:
    f = api.open_initial('demo', p).result(10)
    api.control.request(f.session, 'acquire').result(5)
    keys = [api.command(f.session, 'submit', 'Please say ' + word).result(10)
            for word in ('first', 'second')]
    print(json.dumps(keys), flush=True)
finally:
    api.close_desktop()
'''
        child = subprocess.Popen([sys.executable, "-c", code, str(tmp_path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path)))
        api = None
        try:
            stdout, stderr = child.communicate(json.dumps(dict(session_id="live",
                provider_config=config, environment=environment, timeout=5, context_file=None)),
                timeout=15)
            assert child.returncode == 0, stderr
            keys = json.loads(stdout)
            assert entered.wait(5) and not release.is_set()
            assert process.poll() is None
            api = RemoteFrontend(descriptor, tmp_path, "live")
            frontend = api.open_initial("attach", dict(session_id="live")).result(5)
            token = frontend.session
            assert api.query_input(token, keys[1]).result(5).status.execution == "queued"
            prepared = api.prepare_replay(token, 1, 200).result(5)
            first = drain(api, prepared.stream)
            release.set()
            for key in keys:
                eventually(lambda: api.query_input(token, key).result(5).status.execution
                           == "completed", 15)
            tail = drain(api, prepared.stream)
            assert tail[0].start == first[-1].end + 1
            all_events = [e for batch in first + tail for e in batch.events]
            assert len([e for e in all_events if e["kind"] == "task.completed"]) == 2
            assert len({e["sequence"] for e in all_events}) == len(all_events)
            assert process.pid == descriptor.pid and process.poll() is None
            assert len(gateway.requests) == 2
        finally:
            release.set()
            if api:
                api.close_desktop()
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=3)
