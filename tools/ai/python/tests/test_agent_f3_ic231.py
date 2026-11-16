"""Real F3 side-channel acceptance in a fresh, temporary Virtuoso instance."""
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from sico.core.contracts import BoundContext, NeedsReconcile
from sico.transport.bridge_client import BridgeClient
from sico.transport.bridge_identity import discovery_path
from sico.transport.broker import skill_call_context

CAD = Path(__file__).absolute().parents[3]
IC = Path('/software/eda/cadence/installs/IC23.10.130/tools/dfII/bin/virtuoso')
pytestmark = pytest.mark.skipif(os.environ.get('CAD_AI_RUN_ROUTER_TESTS') != '1' or not IC.is_file(),
                               reason='licensed isolated F3 test requires CAD_AI_RUN_ROUTER_TESTS=1')


def test_live_stages_while_blocked_and_failed_reply_write(tmp_path):
    identity, done = tmp_path / 'identity.json', tmp_path / 'done'
    script = tmp_path / 'startup.il'
    script.write_text('\n'.join([
        f'load({json.dumps(str(CAD / "sico/skill++/SICO.ils"))})',
        'f3Source=makeTable("f3Source" nil) f3Bridge=sicoEnsureBridge(f3Source)',
        f'f3Output=outfile({json.dumps(str(identity))})',
        'fprintf(f3Output "{\\"instance_id\\":\\"%s\\",\\"generation\\":\\"%s\\",\\"snapshot\\":%s}"'
        ' sicoBindings[f3Bridge][\'instanceId] sicoBindings[f3Bridge][\'generation]'
        ' sicoContextJson(sicoBindings[f3Bridge])) close(f3Output)',
        f'procedure(f3Poll() if(isFile({json.dumps(str(done))}) then sicoDisconnect(f3Bridge) exit()'
        ' else hiRegTimer("f3Poll()" 1))) hiRegTimer("f3Poll()" 1)',
    ]) + '\n')
    (tmp_path / 'cds.lib').write_text('')
    env = {k: v for k, v in os.environ.items()
           if k not in {'LD_LIBRARY_PATH', 'LD_PRELOAD', 'PYTHONPATH', 'PYTHONHOME'}
           and not k.startswith(('QT_', 'SICO_'))}
    env.update(SICO_ROOT=str(CAD / 'ai'), CAD_PYTHON=sys.executable, SICO_SKILL_TIMEOUT='20')
    with (tmp_path / 'stdout.log').open('w') as log:
        process = subprocess.Popen([str(IC), '-nographE', '-nocdsinit', '-cdslib',
                                    str(tmp_path / 'cds.lib'), '-restore', str(script),
                                    '-log', str(tmp_path / 'CDS.log')], cwd=tmp_path, env=env,
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)

    def wait_for(observe, timeout=10):
        deadline = time.monotonic() + timeout
        while True:
            result = observe()
            if result:
                return result
            assert process.poll() is None and time.monotonic() < deadline, (tmp_path / 'stdout.log').read_text()
            time.sleep(.05)

    try:
        wait_for(identity.exists, 30)
        record = json.loads(identity.read_text())
        context = BoundContext(record['instance_id'], record['generation'], 'bound', record['snapshot'])
        with BridgeClient.discover(tmp_path, context) as first, BridgeClient.discover(tmp_path, context) as second, ThreadPoolExecutor(1) as pool:
            first.context(context.instance_id)
            first.register_session('f3-owner', context)
            second.register_session('f3-other', context)
            ids = []

            def call(code):
                with skill_call_context(session_id='f3-owner', progress=lambda row: ids.append(row['request_id'])):
                    return first.assistant_call(context, 'eval_skill', code)

            def receipt(rid):
                return first.request_receipt(context, rid, session_id='f3-owner')

            future = pool.submit(call, 'progn(printf("F3_OUTPUT_PRIVATE\\n") '
                                 'warn("F3_WARNING_PRIVATE\\n") ipcSleep(3) "F3_DELAY_PRIVATE")')
            rid = wait_for(lambda: ids[-1] if ids else None)
            started = wait_for(lambda: (row if row.get('skill_stage', {}).get('event') == 'skill.evaluation_started' else None)
                               if (row := receipt(rid)) else None)
            assert started['state'] == 'running' and 'reply' not in started and not future.done()
            captured = future.result(8)
            assert captured['value'] == 'F3_DELAY_PRIVATE'
            assert captured['output'] == 'F3_OUTPUT_PRIVATE\n*WARNING* F3_WARNING_PRIVATE\n'
            assert captured['output_capture'] == 'skill_ports'
            wait_for(lambda: receipt(rid).get('skill_stage', {}).get('event') == 'skill.reply_write_succeeded')
            failed = call('error("F3_EXPECTED_FAILURE")')
            assert failed['ok'] is False
            assert '*Error* F3_EXPECTED_FAILURE' in failed['output']
            failed_id = ids[-1]
            wait_for(lambda: receipt(failed_id).get('skill_stage', {}).get('event') == 'skill.reply_write_succeeded')
            failure = receipt(failed_id)
            assert any(r['code'] == 'evaluator_failed' for r in failure['skill_stages'])
            assert call('42')['output'] == ''
            started_at = time.monotonic()
            with pytest.raises(NeedsReconcile, match='skill_reply_write_failed'):
                call("progn(procedure(f3DropReply(dropCid dropText) nil) putd('sicoIpcWrite getd('f3DropReply)) 42)")
            write_id = ids[-1]
            elapsed = time.monotonic() - started_at
            assert elapsed < 5
            lost = receipt(write_id)
            assert lost['state'] == 'timed_out_unknown' and lost['skill_stage']['event'] == 'skill.reply_write_failed'
            assert 'reply' not in lost and not lost['automatic_resume_allowed']
            with pytest.raises(NeedsReconcile, match='skill_blocked_unknown'):
                second.get_context(context)
            path = discovery_path(tmp_path, context).with_suffix('.skill.jsonl')
            assert path.stat().st_mode & 0o077 == 0
            assert 'F3_DELAY_PRIVATE' not in path.read_text() and 'F3_EXPECTED_FAILURE' not in path.read_text()
            (tmp_path / 'acceptance.json').write_text(json.dumps(dict(
                during_delay=started, failure=failure, write_failure=lost, write_failure_seconds=elapsed), indent=2))
        done.touch()
        assert process.wait(timeout=15) == 0
        archived = BridgeClient.archived_receipt(tmp_path, context, write_id, session_id='f3-owner')
        assert archived['skill_stage']['event'] == 'skill.reply_write_failed'
    finally:
        if process.poll() is None:
            done.touch()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
