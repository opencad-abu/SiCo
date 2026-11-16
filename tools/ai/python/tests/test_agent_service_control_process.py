"""An independent client cannot resume writes after another process takes control."""

import os
import subprocess
import sys

from agent_service_launch_fixtures import eventually
from agent_service_lifecycle_fixtures import service_process

from sico.service.remote_frontend import RemoteFrontend


def test_process_takeover_rejects_old_writer_and_exit_leaves_session_alive(tmp_path):
    with service_process(tmp_path) as (service, descriptor):
        api = RemoteFrontend(descriptor, tmp_path, "live")
        child = None
        try:
            frontend = api.open_initial("demo", dict(session_id="live", provider_config=None,
                environment={}, timeout=5, context_file=None)).result(10)
            token = frontend.session
            code = '''
import sys
from pathlib import Path
from sico.service.remote_frontend import RemoteFrontend
from sico.service.service_discovery import discover_project
project = Path(sys.argv[1])
api = RemoteFrontend(discover_project(project).service, project, 'live')
try:
    frontend = api.open_initial('attach', dict(session_id='live')).result(5)
    api.control.request(frontend.session, 'acquire').result(5)
    (project/'client-ready').write_text('ready')
    assert sys.stdin.readline() == 'send\\n'
    try:
        api.command(frontend.session, 'submit', 'stale process request').result(5)
    except ValueError:
        print('OLD_CONTROL_REJECTED', flush=True)
    else:
        raise AssertionError('Old client executed after takeover')
finally:
    api.close_desktop()
'''
            child = subprocess.Popen([sys.executable, "-c", code, str(tmp_path)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, env=dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path)))
            eventually(lambda: (tmp_path / "client-ready").exists(), 10)
            assert api.control.request(token).result(5).state == "occupied"
            api.control.request(token, "takeover").result(5)
            stdout, stderr = child.communicate("send\n", timeout=10)
            assert child.returncode == 0, stderr
            assert "OLD_CONTROL_REJECTED" in stdout
            key = api.command(token, "submit", "new controller request").result(5)
            eventually(lambda: api.query_input(token, key).result(5).status.execution
                       == "completed")
            assert service.poll() is None
        finally:
            api.close_desktop()
            if child is not None and child.poll() is None:
                child.kill()
                child.communicate(timeout=3)
