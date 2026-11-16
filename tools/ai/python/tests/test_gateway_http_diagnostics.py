"""A provider rejection must survive flat + worker relay without a transport error."""
import json
import os
import sys
import time
from types import SimpleNamespace

from cadai.responses_wrapper import ResponsesWrapper
from sico.local_process import LocalProcess
from sico.codex.lsf_gateway import LsfGateway
from sico.codex.turn_error import failure_message
from test_agent_codex_protocol import gateway, post
from test_lsf_gateway import PipeJob
from test_agent_codex_native import native_codex


class SlowErrorJob(PipeJob):
    def __init__(self, environment, cwd, _evidence):
        self.process = LocalProcess([sys.executable, '-s', '-c', '''
import sys,time
from sico.codex.gateway_wire import Frames,send,encode,chunk
r=Frames(sys.stdin.buffer);o=sys.stdout.buffer
send(o,encode('ready'))
while r.next(5).get('kind')!='end':pass
send(o,encode('headers',status=400,content='application/json'))
time.sleep(0.4)
send(o,chunk(b'{"error":{"message":"Upstream HTTP 400"}}'))
send(o,encode('end'))
'''], environment, cwd)
        self.closed = False
        self.instances.append(self)


def settings(endpoint):
    return SimpleNamespace(environment=dict(os.environ), endpoint=endpoint,
                           api_key='private-upstream-key', timeout=3, idle_timeout=3)


def wait_closed(relay):
    deadline = time.monotonic() + 5
    while relay.active and time.monotonic() < deadline:
        time.sleep(.02)
    assert not relay.active


def test_delayed_error_body_does_not_turn_400_into_lsf_failure(tmp_path):
    relay = LsfGateway(settings('https://unused/v1/responses'), tmp_path, tmp_path,
                       job_factory=SlowErrorJob)
    try:
        with ResponsesWrapper(relay.base_url, relay.token, timeout=5) as wrapper:
            status, body = post(wrapper)
            assert status == 400 and b'400' in body
            wait_closed(relay)
            assert 'unavailable' not in relay.diagnostic
            message = failure_message({'codexErrorInfo': 'other'}, wrapper=wrapper,
                                      gateway=relay, tool_format='flat')
            assert 'HTTP 400' in message and '请求字段' in message
    finally:
        relay.close()


def test_upstream_rejection_metadata_survives_both_relays(tmp_path, gateway):
    gateway.reply = (400, 'application/json', json.dumps({'error': {
        'code': 'unsupported_parameter', 'type': 'invalid_request_error',
        'param': 'tools[0].type', 'message': 'private-upstream-key secret prompt',
    }}).encode())
    relay = LsfGateway(settings(f'http://127.0.0.1:{gateway.server_port}/v1'),
                       tmp_path, tmp_path, job_factory=PipeJob)
    try:
        with ResponsesWrapper(relay.base_url, relay.token, timeout=5) as wrapper:
            status, raw = post(wrapper)
            wait_closed(relay)
            assert status == 400
            assert b'unsupported_parameter' in raw and b'tools[0].type' in raw
            assert b'request-123' in raw
            assert b'secret prompt' not in raw and b'private-upstream-key' not in raw
            assert 'Upstream returned HTTP 400' in relay.diagnostic
            message = failure_message({'codexErrorInfo': 'other'}, wrapper=wrapper,
                                      gateway=relay, tool_format='flat')
            assert 'HTTP 400' in message and '请求字段' in message
            assert len(gateway.requests) == 1
            gateway.reply = (200, 'application/json', b'{"output":[]}')
            assert post(wrapper)[0] == 200
            wait_closed(relay)
            assert wrapper.diagnostic == relay.diagnostic == ''
            assert wrapper.http_status is relay.http_status is None
    finally:
        relay.close()


def test_queue_submission_error_is_not_an_upstream_http_rejection(tmp_path):
    def unavailable(*_):
        raise OSError('secret scheduler text')
    relay = LsfGateway(settings('https://unused/v1'), tmp_path, tmp_path, job_factory=unavailable)
    try:
        with ResponsesWrapper(relay.base_url, relay.token, timeout=5) as wrapper:
            status, raw = post(wrapper)
            wait_closed(relay)
            assert status == 502
            assert relay.http_status is None
            assert relay.diagnostic == 'LSF AI gateway failed during queue submission'
            assert b'secret scheduler text' not in raw
    finally:
        relay.close()


def test_real_codex_flat_lsf_rejection_keeps_actionable_status(tmp_path, gateway, monkeypatch, native_codex):
    from sico.codex import lsf_gateway
    from sico.codex.backend import CodexBackend
    from sico.core.tools import ToolRegistry
    from sico.storage.journal import SessionJournal
    from test_agent_codex_native import CONTEXT, settings as provider_settings

    gateway.reply = (400, 'application/json', json.dumps({'error': {
        'code': 'unsupported_parameter', 'param': 'reasoning.effort',
        'message': 'copilot-test-secret do not display private prompt',
    }}).encode())
    provider = provider_settings(gateway, native_codex, 'switch')
    provider.environment['SICO_CODEX_LSF_EXE'] = '/bin/true'
    monkeypatch.setattr(lsf_gateway, 'LsfGateway',
                        lambda *args, **kwargs: LsfGateway(
                            *args, job_factory=PipeJob, **kwargs))
    with SessionJournal(tmp_path, 'gateway-failure') as journal:
        backend = CodexBackend(provider, ToolRegistry(), journal, CONTEXT)
        try:
            events = list(backend.run('Synthetic error test', input_id='reject'))
            assert events[-1]['kind'] == 'task.failed'
            message = events[-1]['payload']['message']
            assert 'HTTP 400' in message and 'reasoning.effort' in message
            assert 'unsupported_parameter' in message and '请求字段' in message
            assert 'unavailable' not in message
            assert len(gateway.requests) == 1
            assert 'copilot-test-secret' not in json.dumps(journal.events())
            assert 'private prompt' not in json.dumps(journal.events())
        finally:
            backend.close()
