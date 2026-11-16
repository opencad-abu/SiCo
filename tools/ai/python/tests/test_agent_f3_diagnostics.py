"""F3 boundary acceptance in isolated processes, without user design access."""
import json
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from cadai.skill_check import check_source

ROOT = Path(__file__).resolve().parents[2]
DB = Path('/software/eda/cadence/installs/IC23.10.130/tools/dfII/bin/dbAccess')
IDENTITY = dict(instance_id='f3-instance', generation='f3-generation',
                bridge_id='b' * 32, router_id='c' * 32)


def native(tmp_path, body):
    if not DB.is_file():
        pytest.skip('dbAccess unavailable')
    diagnostic = tmp_path / 'skill.jsonl'
    diagnostic.touch(mode=0o600)
    lines = []
    for name in ('AI_protocol.il', 'AI_eval.il', 'AI_copilot_literals.il',
                 'SICO_diagnostics.il', 'SICO_context.il', 'SICO_sessions.il'):
        module = (ROOT.parent / 'sico' if name.startswith('SICO_') else ROOT) / 'skill' / name
        assert check_source(module.read_bytes(), source_name=name)['ok']
        lines.append(f'load({json.dumps(str(module))})')
    lines += [
        'f3Binding=makeTable("f3Binding" nil) sicoBindings["f3-cid"]=f3Binding',
        f'f3Binding[\'cwd]={json.dumps(str(tmp_path))}',
        f'f3Binding[\'diagnosticPath]={json.dumps(str(diagnostic))}',
        'f3Binding[\'instanceId]="f3-instance" f3Binding[\'generation]="f3-generation"',
        f'f3Binding[\'bridgeId]="{IDENTITY["bridge_id"]}"',
        f'f3Binding[\'routerId]="{IDENTITY["router_id"]}"',
        'procedure(f3Write(cid data) aiWriteTextFile("replies.jsonl"'
        ' strcat(or(aiReadTextFile("replies.jsonl") "") data)) t)',
        "putd('sicoIpcWrite getd('f3Write))",
    ]
    lines += body + ['aiWriteTextFile("passed" "yes") exit()']
    script = tmp_path / 'probe.il'
    script.write_text('\n'.join(lines) + '\n')
    (tmp_path / 'cds.lib').write_text('')
    env = {k: v for k, v in os.environ.items()
           if k not in {'LD_LIBRARY_PATH', 'LD_PRELOAD', 'PYTHONPATH', 'PYTHONHOME'}
           and not k.startswith(('QT_', 'SICO_'))}
    result = subprocess.run([str(DB), '-nocdsinit', '-cdslib', str(tmp_path / 'cds.lib'),
                             '-load', str(script)], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=30)
    output = result.stdout + result.stderr
    (tmp_path / 'native.log').write_text(output)
    assert result.returncode == 0 and (tmp_path / 'passed').exists(), output
    assert '*Error* load:' not in output and '(reader)' not in output, output
    rows = [json.loads(line) for line in diagnostic.read_text().splitlines()]
    for row in rows:
        assert all(row[key] == value for key, value in IDENTITY.items())
        assert len(json.dumps(row).encode()) <= 8192
    path = tmp_path / 'replies.jsonl'
    return rows, [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


@pytest.mark.parametrize('split', [False, True])
def test_overflow_discards_one_frame_and_recovers_same_chunk(tmp_path, split):
    first, next_id, last = 'a' * 32, 'd' * 32, 'e' * 32
    body = [f'sicoData("f3-cid" "{first} get_context ")',
            'f3Long="x" for(f3Index 1 18 f3Long=strcat(f3Long f3Long))']
    if split:
        body += ['sicoData("f3-cid" f3Long)',
                 'unless(and(f3Binding[\'discarding] f3Binding[\'buffer]=="") error("No discard state"))',
                 f'sicoData("f3-cid" "{last} get_context")']
        chunk = f'"\\n{next_id} get_context\\n"'
    else:
        chunk = f'strcat(f3Long "\\n{next_id} get_context\\n")'
    body += [f'sicoData("f3-cid" {chunk})',
             'unless(and(!f3Binding[\'discarding] f3Binding[\'buffer]=="") error("Recovery failed"))',
             f'sicoData("f3-cid" "{last} get_context\\n")']
    rows, replies = native(tmp_path, body)
    assert [row['id'] for row in replies] == [first, next_id, last]
    assert replies[0]['code'] == 'request_too_large'
    assert replies[1]['result']['valid'] and replies[2]['result']['valid']
    assert not any(row['event'] == 'skill.request_parsed' and row['request_id'] == first for row in rows)
    assert sum(row['event'] == 'skill.request_overflow' for row in rows) == 1


def test_aggregate_limit_is_per_frame_and_invalid_id_is_not_echoed(tmp_path):
    rows, replies = native(tmp_path, [
        f'f3Frame="{"a" * 32} unsupported "',
        'f3Pad="x" for(f3Index 1 16 f3Pad=strcat(f3Pad f3Pad))',
        'f3Frame=strcat(f3Frame f3Pad " extra\\n")',
        'sicoData("f3-cid" strcat(f3Frame f3Frame f3Frame f3Frame))',
        'sicoData("f3-cid" "SECRET_INVALID_ID hidden_payload\\n")',
    ])
    assert len(replies) == 4 and all(row['code'] == 'invalid_request' for row in replies)
    assert not any(row['event'] == 'skill.request_overflow' for row in rows)
    assert 'SECRET' not in json.dumps(rows) and 'hidden_payload' not in json.dumps(rows)


@pytest.mark.parametrize('method', ['eval_skill', 'eval_skill_native', 'load_skill_file'])
@pytest.mark.parametrize('succeed', [True, False])
def test_real_evaluators_report_stages_without_payload(tmp_path, method, succeed):
    code = '"F3_PRIVATE_VALUE"' if succeed else 'error("F3_PRIVATE_ERROR")'
    source = tmp_path / 'source.il'
    source.write_text(code)
    encoded = (str(source) if method == 'load_skill_file' else code).encode().hex()
    rid = 'a' * 32
    rows, replies = native(tmp_path, [
        f'sicoData("f3-cid" "{rid} assistant_call bound {method} {encoded}\\n")',
    ])
    assert replies[0]['ok'] and replies[0]['result']['ok'] is succeed
    if method == 'load_skill_file':
        assert replies[0]['result'].get('value' if succeed else 'error')
    else:
        assert ('F3_PRIVATE_VALUE' if succeed else 'F3_PRIVATE_ERROR') in json.dumps(replies)
    assert [row['event'] for row in rows] == [
        'skill.request_parsed', 'skill.source_spooled', 'skill.evaluation_started',
        'skill.evaluation_finished', 'skill.reply_prepared',
        'skill.reply_write_started', 'skill.reply_write_succeeded']
    assert rows[3]['code'] == ('ok' if succeed else 'evaluator_failed')
    assert all(row['target_id'] == 'bound' and row['request_id'] == rid for row in rows)
    assert 'F3_PRIVATE' not in json.dumps(rows) and encoded not in json.dumps(rows)


@pytest.mark.parametrize('mode', ['nil', 'exception'])
def test_write_failure_persists_and_rejection_skips_eval(tmp_path, mode):
    write = 'nil' if mode == 'nil' else 'error("private write exception")'
    rows, replies = native(tmp_path, [
        f'procedure(f3BrokenWrite(cid data) {write})',
        "putd('sicoIpcWrite getd('f3BrokenWrite))",
        f'sicoData("f3-cid" "{"a" * 32} assistant_call bound eval_skill 31\\n")',
        f'sicoData("f3-cid" "{"b" * 32} assistant_call bound invalid 31\\n")',
    ])
    assert replies == []
    assert [r['event'] for r in rows if r['request_id'] == 'a' * 32][-1] == 'skill.reply_write_failed'
    rejected = [r['event'] for r in rows if r['request_id'] == 'b' * 32]
    assert 'skill.request_rejected' in rejected and 'skill.evaluation_started' not in rejected
    assert 'private write exception' not in json.dumps(rows)


def test_bridge_stage_identity_and_write_failure_are_evidence_not_a_reply(tmp_path):
    from test_agent_bridge import bound

    from sico.transport.bridge_client import BridgeClient
    from sico.transport.instance_bridge import InstanceBridge
    from sico.transport.skill_diagnostics import SkillDiagnostics

    started, finish = threading.Event(), threading.Event()
    with InstanceBridge(tmp_path, bound()) as bridge, ThreadPoolExecutor(1) as pool:
        def operation(rid):
            bridge.router.skill_started(rid)
            started.set()
            finish.wait(5)
            raise RuntimeError('test interrupted after write failure')
        future = pool.submit(bridge.router.execute, 'get_context', operation, wait=True,
                             session_id='owner', target_id='bound')
        try:
            assert started.wait(2)
            rid = bridge.router.skill_pending()
            event = dict(protocol='cad_ai_skill_stage.v1', **bridge.journal.identity,
                         event='skill.reply_write_failed', request_id=rid, method='get_context',
                         target_id='bound', code='ipc_write_failed', sequence=1,
                         field_count=2, payload_length=42)
            assert not bridge.router.record_skill_stage(dict(event, generation='old'))
            assert not bridge.router.record_skill_stage(dict(event, target_id='foreign'))
            path = bridge.path.with_suffix('.skill.jsonl')
            with SkillDiagnostics(path, bridge.router) as monitor:
                assert path.stat().st_mode & 0o077 == 0
                with path.open('a') as log:
                    log.write(json.dumps(event) + '\n')
                monitor.poll()
                assert bridge.router.snapshot()['unknown'] == rid
                receipt = bridge.journal.receipt(rid, session_id='owner', target_id='bound')
                assert receipt['skill_stage']['event'] == 'skill.reply_write_failed'
                assert 'reply' not in receipt and not receipt['automatic_resume_allowed']
        finally:
            finish.set()
        with pytest.raises(RuntimeError):
            future.result(2)
    archived = BridgeClient.archived_receipt(tmp_path, bound(), rid, session_id='owner')
    assert archived['skill_stage']['event'] == 'skill.reply_write_failed'
    assert archived['state'] == 'timed_out_unknown'


@pytest.mark.parametrize('kind', ['spool', 'evaluator', 'diagnostic'])
def test_diagnostic_failures_never_mask_business_outcomes(tmp_path, kind):
    body = []
    if kind == 'spool':
        body += ["f3OriginalWrite=getd('aiWriteTextFile) f3FailSpool=t",
                 'procedure(f3SpoolWrite(fpPath fpText)'
                 ' if(f3FailSpool then f3FailSpool=nil nil else'
                 ' apply(f3OriginalWrite list(fpPath fpText))))',
                 "putd('aiWriteTextFile getd('f3SpoolWrite))"]
    elif kind == 'evaluator':
        body += ['procedure(f3BadEval(fpSource fpOutput) error("F3_EVAL_EXCEPTION"))',
                 "putd('aiEvalExpression getd('f3BadEval))"]
    else:
        body += ['procedure(f3BadDiagnostic(@rest fpValues) error("F3_LOG_FAILURE"))',
                 "putd('sicoDiagnostic getd('f3BadDiagnostic))"]
    body += [f'sicoData("f3-cid" "{"a" * 32} assistant_call bound eval_skill 31\\n")']
    rows, replies = native(tmp_path, body)
    if kind == 'diagnostic':
        assert replies[0]['result']['value'] == '1' and rows == []
    else:
        assert replies[0]['code'] == 'assistant_failed'
        assert any(row['event'] == ('skill.request_rejected' if kind == 'spool' else 'skill.evaluation_failed')
                   for row in rows)
        if kind == 'evaluator':
            assert 'F3_EVAL_EXCEPTION' in replies[0]['error']


@pytest.mark.parametrize('code', ['skill_timeout', 'skill_response_pending', 'skill_reply_write_failed'])
def test_notice_after_mailbox_timeout_does_not_become_late_reply(tmp_path, code):
    from test_agent_bridge import bound, connect_peer, reply

    from sico.transport.bridge_client import BridgeClient
    from sico.transport.instance_bridge import InstanceBridge
    from sico.transport.methods import QueryUnavailable

    with InstanceBridge(tmp_path, bound(), operation_timeout=1) as bridge, ThreadPoolExecutor(1) as pool:
        peer = connect_peer(bridge.broker, bound())
        try:
            with BridgeClient.discover(tmp_path, bound()) as client:
                client.register_session('owner', bound())
                future = pool.submit(client.get_context, bound())
                request = peer.receive()
                with pytest.raises(QueryUnavailable) as caught:
                    future.result(8)
                assert caught.value.code == 'skill_timeout'
                peer.send(dict(protocol='cad_ai_transport.v1', kind='response', generation='g',
                               id=request['id'], ok=False, code=code))
                time.sleep(.1)
                receipt = client.request_receipt(bound(), request['id'], session_id='owner')
                assert receipt['state'] == 'timed_out_unknown' and 'reply' not in receipt
                assert client.router_snapshot(bound())['unknown'] == request['id']
                reply(peer, request, 'actual late result')
                deadline = time.monotonic() + 2
                while client.router_snapshot(bound())['unknown']:
                    assert time.monotonic() < deadline
                    time.sleep(.01)
                assert client.request_receipt(bound(), request['id'], session_id='owner')['state'] == 'late_reply'
        finally:
            peer.close()


def test_partial_stage_record_private_file_and_out_of_order_events(tmp_path):
    from test_agent_bridge import bound

    from sico.transport.instance_bridge import InstanceBridge
    from sico.transport.skill_diagnostics import SkillDiagnostics

    with InstanceBridge(tmp_path, bound()) as bridge:
        rid = 'a' * 32
        bridge.journal.record(dict(request_id=rid, session_id='owner', target_id='bound',
                                   method='get_context', state='queued'))
        event = dict(protocol='cad_ai_skill_stage.v1', **bridge.journal.identity,
                     request_id=rid, target_id='bound', method='get_context', sequence=2,
                     field_count=1, payload_length=32, code='', event='skill.request_parsed')
        assert not bridge.router.record_skill_stage(event)
        bridge.journal.record(dict(request_id=rid, started_at=time.time(), state='running'))
        path = bridge.path.with_suffix('.skill.jsonl')
        with SkillDiagnostics(path, bridge.router) as monitor:
            data = json.dumps(event)
            with path.open('a') as stream:
                stream.write(data[:50])
            monitor.poll()
            assert 'skill_stage' not in bridge.journal.lookup(rid)
            with path.open('a') as stream:
                stream.write(data[50:] + '\n')
            monitor.poll()
            assert bridge.router.record_skill_stage(event)
            assert not bridge.router.record_skill_stage(dict(event, sequence=1))
            assert not bridge.router.record_skill_stage(dict(event, bridge_id='wrong'))
            assert len(bridge.journal.lookup(rid)['skill_stages']) == 1
        link = bridge.path.with_suffix('.symlink')
        link.symlink_to(path)
        with pytest.raises((OSError, ValueError)):
            SkillDiagnostics(link, bridge.router)
