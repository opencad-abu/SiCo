"""B1 process lifecycle over actual loopback sockets; no user design access."""
import hashlib
import json
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


@pytest.fixture
def runtime(tmp_path):
    from sico.service.background_worker import BackgroundRuntime
    executable = tmp_path / 'virtuoso'
    executable.write_text('#!' + sys.executable + '\n' + '''
import json, os, socket, struct, time
from pathlib import Path
config = json.loads(Path('connection.json').read_text())
job = json.loads(Path('job.json').read_text())
mode = Path(__file__).with_name('mode').read_text()
if mode == 'exit': raise SystemExit(7)
if mode == 'hang':
    import signal
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(30)
s = socket.create_connection((config['host'], config['port']))
def send(data):
    raw = json.dumps(data).encode(); s.sendall(struct.pack('!I', len(raw)) + raw)
def read(n):
    data = b''
    while len(data) < n:
        part = s.recv(n-len(data))
        if not part: raise SystemExit(0)
        data += part
    return data
def receive(): return json.loads(read(struct.unpack('!I', read(4))[0]))
context = job['context']
send(dict(protocol='cad_ai_transport.v1', kind='hello', token=config['token'],
          instance_id=context['instance_id'], generation=context['generation'], contexts=[context]))
receive()
while True:
    req = receive()
    if req['method'] == 'get_context':
        result = dict(context['snapshot'], pid=os.getpid(), version='test-version')
        if mode == 'identity': result['worker_id'] = 'other'
    else:
        Path('dispatched').write_text(req['id'])
        target = job['target']
        result = dict(protocol='cad_ai_background_probe.v1', ok=True, target=target,
                      virtuoso_version='test-version', open_mode='r', modified=False,
                      closed=True, window_required=False, callbacks_executed=False,
                      technology_library='Tech', instance_count=0, dependencies=[],
                      inspection=dict(ok=True, kind='schematic', cellview=target,
                                      modified=False, truncated=False, instances=[], nets=[], terminals=[]))
        if mode == 'invalid': result['target'] = dict(target, cell='other')
        if mode == 'delay': time.sleep(2)
        if mode == 'timeout_reply':
            send(dict(protocol='cad_ai_transport.v1', kind='response', generation=context['generation'],
                      id=req['id'], ok=False, code='skill_timeout'))
            continue
    send(dict(protocol='cad_ai_transport.v1', kind='response', generation=context['generation'],
              instance_id=context['instance_id'], id=req['id'], ok=True, result=result))
''')
    executable.chmod(0o700)
    (tmp_path / 'mode').write_text('ok')
    context = tmp_path / 'cadB1.cxt'
    context.write_bytes(b'test-context')
    library = tmp_path / 'library'
    library.mkdir()
    return BackgroundRuntime(virtuoso=executable, context=context,
                             context_sha256=hashlib.sha256(context.read_bytes()).hexdigest(),
                             version='test-version', libraries={'Work': library},
                             xvfb=Path('/bin/true'))


def worker(tmp_path, runtime, **kwargs):
    from sico.service.background_worker import BackgroundWorker
    root = tmp_path / 'jobs'
    root.mkdir(mode=0o700, exist_ok=True)
    return BackgroundWorker(root, runtime, startup_timeout=2, execution_timeout=1,
                            shutdown_timeout=.2, **kwargs)


def assert_reaped(receipt):
    assert receipt['cleanup']['process_reaped']
    assert receipt['cleanup']['broker_closed']
    with socket.socket() as sock:
        assert sock.connect_ex(('127.0.0.1', receipt['port'])) != 0
    assert not Path('/proc', str(receipt['pid'])).exists()


def test_two_independent_workers_have_isolated_identity_and_verified_artifact(tmp_path, runtime):
    from sico.service.background_worker import read_background_artifact
    a, b = worker(tmp_path, runtime), worker(tmp_path, runtime)
    with ThreadPoolExecutor(2) as pool:
        receipts = list(pool.map(lambda w: w.run('inspect_schematic', {'lib': 'Work', 'cell': 'top'}), (a, b)))
    for key in ('worker_id', 'job_id', 'instance_id', 'generation', 'bridge_id', 'router_id', 'session_id', 'target_id', 'pid', 'port'):
        assert receipts[0][key] != receipts[1][key]
    for w, receipt in zip((a, b), receipts):
        assert receipt['state'] == 'completed', receipt
        assert receipt['snapshot_scope'] == 'saved_oa'
        assert not receipt['automatic_resume_allowed']
        assert_reaped(receipt)
        assert read_background_artifact(w.directory)['inspection']['cellview']['cell'] == 'top'
        assert not (w.directory / 'connection.json').exists()
        assert not (w.directory / 'bootstrap.il').exists()
        assert w.directory.stat().st_mode & 0o777 == 0o700
        assert (w.directory / 'receipt.json').stat().st_mode & 0o777 == 0o600
        with pytest.raises(RuntimeError):
            w.run('inspect_schematic', {'lib': 'Work', 'cell': 'top'})


@pytest.mark.parametrize('mode, state', [('exit', 'background_unavailable'),
                                       ('hang', 'background_unavailable'),
                                       ('identity', 'background_unavailable'),
                                       ('invalid', 'background_unavailable'),
                                       ('delay', 'worker_timeout'), ('timeout_reply', 'worker_timeout')])
def test_failure_reaps_process_and_does_not_publish_success(tmp_path, runtime, mode, state):
    (runtime.virtuoso.parent / 'mode').write_text(mode)
    w = worker(tmp_path, runtime)
    started = time.monotonic()
    receipt = w.run('inspect_schematic', {'lib': 'Work', 'cell': 'top'})
    assert time.monotonic() - started < 6
    assert receipt['state'] == state and not receipt['ok'], receipt
    assert_reaped(receipt)
    assert not receipt.get('artifact')
    if mode in {'identity', 'exit', 'hang'}:
        assert not (w.directory / 'dispatched').exists()


def test_runtime_and_request_validation_precedes_spawn(tmp_path, runtime, monkeypatch):
    import sico.service.background_worker as module
    monkeypatch.setattr(module.subprocess, 'Popen', lambda *a, **k: pytest.fail('Unexpected spawn'))
    w = worker(tmp_path, runtime)
    with pytest.raises(ValueError):
        w.run('eval_skill', {'code': 'anything'})
    runtime.context.write_bytes(b'changed')
    receipt = worker(tmp_path, runtime).run('inspect_schematic', {'lib': 'Work', 'cell': 'top'})
    assert receipt['state'] == 'background_unavailable' and receipt['reason'] == 'context_checksum'


def test_environment_and_retained_state_are_private(tmp_path, runtime):
    from sico.service.background_worker import worker_environment
    env = worker_environment(tmp_path, {'DISPLAY': ':2', 'SICO_ROOT': '/real',
                            'CDS_NOGRAPH_DISPLAY': ':2', 'CDS_LIC_FILE': 'license',
                            'PYTHONPATH': '/old', 'PATH': '/bin', 'LD_LIBRARY_PATH': '/old'})
    assert not set(env) & {'DISPLAY', 'CDS_NOGRAPH_DISPLAY', 'SICO_ROOT', 'PYTHONPATH', 'LD_LIBRARY_PATH'}
    assert env['CDS_LIC_FILE'] == 'license'
    w = worker(tmp_path, runtime)
    receipt = w.run('inspect_schematic', {'lib': 'Work', 'cell': 'top'})
    rows = [json.loads(line)['record'] for line in (w.directory / 'router/requests.jsonl').read_text().splitlines()]
    assert rows and all(row['execution_mode'] == 'background' for row in rows)
    assert any(row['request_id'] == receipt['request_id'] and row['state'] == 'completed' for row in rows)
    assert not (w.directory / 'scratch').exists()
    assert receipt['cleanup']['guardian_reaped']


def test_failed_stop_file_does_not_skip_process_cleanup(tmp_path, runtime, monkeypatch):
    import sico.service.background_worker as module
    original = module._write
    def fail_stop(path, value):
        if path.name == 'stop':
            raise OSError('Injected stop-file failure')
        return original(path, value)
    monkeypatch.setattr(module, '_write', fail_stop)
    w = worker(tmp_path, runtime)
    receipt = w.run('inspect_schematic', {'lib': 'Work', 'cell': 'top'})
    assert_reaped(receipt)


def test_supervisor_exit_reaps_stuck_startup_process(tmp_path, runtime):
    import subprocess

    from sico.service.background_worker import _process_start
    (runtime.virtuoso.parent / 'mode').write_text('hang')
    driver = tmp_path / 'driver.py'
    driver.write_text('''
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from sico.service.background_worker import BackgroundRuntime, BackgroundWorker
import hashlib
root=Path(sys.argv[2])
ctx=root/'cadB1.cxt'
r=BackgroundRuntime(root/'virtuoso', ctx, hashlib.sha256(ctx.read_bytes()).hexdigest(),
                    'test-version', {'Work':root/'library'}, Path('/bin/true'))
BackgroundWorker(root/'orphan', r, startup_timeout=20).run('inspect_schematic', {'lib':'Work','cell':'top'})
''')
    parent = subprocess.Popen([sys.executable, str(driver), str(Path(__file__).resolve().parents[1]), str(tmp_path)])
    receipt = None
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            paths = list((tmp_path / 'orphan').glob('*/receipt.json'))
            if paths:
                receipt = json.loads(paths[0].read_text())
                if receipt.get('guardian_pid'):
                    break
            time.sleep(.05)
        assert receipt and receipt.get('guardian_pid')
        launched = receipt['launcher_pid']
        epoch = _process_start(launched)
        parent.kill()
        parent.wait(timeout=2)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                fields = Path('/proc', str(launched), 'stat').read_text().rsplit(')', 1)[1].split()
                if fields[0] == 'Z' or fields[19] != epoch:
                    break
            except (FileNotFoundError, ProcessLookupError):
                break
            time.sleep(.05)
        else:
            pytest.fail('Orphan Virtuoso was not stopped')
        assert not list((tmp_path / 'orphan').glob('*/artifacts/result.json'))
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=2)


@pytest.mark.parametrize('tamper', ['artifact', 'identity', 'path', 'symlink'])
def test_artifact_reader_rejects_tampering(tmp_path, runtime, tamper):
    from sico.service.background_worker import read_background_artifact
    w = worker(tmp_path, runtime)
    receipt = w.run('inspect_schematic', {'lib': 'Work', 'cell': 'top'})
    artifact = w.directory / receipt['artifact']['path']
    if tamper == 'artifact':
        artifact.write_text('{}')
    elif tamper == 'identity':
        receipt['job_id'] = 'f' * 32
    elif tamper == 'path':
        receipt['artifact']['path'] = '../outside.json'
    else:
        artifact.unlink()
        artifact.symlink_to(w.directory / 'job.json')
    (w.directory / 'receipt.json').write_text(json.dumps(receipt))
    with pytest.raises(ValueError):
        read_background_artifact(w.directory)
