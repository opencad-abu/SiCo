"""Scheduler command identity, bounded transport and deterministic LSF parsing."""

import json
import os
import sys
import time
from dataclasses import replace

import pytest
from agent_lsf_fixtures import config

from sico.lsf.commands import command_environment, run_command
from sico.lsf.output import history, jobs, submitted
from sico.lsf.job_control import LsfJobControl
from sico.service.lsf_scheduler import LsfScheduler
from sico.transport.framing import ProtocolError


def test_lsf_10_fields_fallback_and_cancel_filters_stable_name(tmp_path):
    calls = []
    name = 'sico-' + 'a' * 32
    expected = dict(cluster='eda-test', owner=config(tmp_path).owner, name=name,
                    job_id='42', state='RUN')
    def runner(argv, **kwargs):
        calls.append(argv)
        assert kwargs['environment'].get('SICO_API_KEY') is None
        if argv[0] == '/fake/lsid':
            return 0, 'IBM Spectrum LSF 10.1\nMy cluster name is eda-test\n'
        if '-json' in argv:
            return 255, 'illegal option -- j'
        if argv[0] == '/fake/bjobs':
            assert 'delimiter="|"' in argv[-1]
            return 0, '42|' + expected['owner'] + '|RUN|' + name + '\n'
        return 0, 'Job <42> is being terminated'
    scheduler = LsfJobControl(config(tmp_path), environment={'SICO_API_KEY': 'secret'},
                             runner=runner)
    assert scheduler.observe(expected)['state'] == 'RUN'
    assert scheduler.cancel(expected) == 'requested'
    assert calls[-1] == ['/fake/bkill', '-J', name, '0']
    assert sum('-json' in call for call in calls) == 1


def test_cluster_change_and_recycled_job_id_never_cancel(tmp_path):
    calls = []
    row = dict(cluster='eda-test', owner=config(tmp_path).owner, name='sico-original',
               job_id='42', state='RUN')
    def runner(argv, **kwargs):
        calls.append(argv)
        if argv[0] == '/fake/lsid':
            return 0, 'My cluster name is eda-test\n'
        return 0, json.dumps({'RECORDS': [dict(JOBID='43', USER=row['owner'],
                                               STAT='RUN', JOB_NAME=row['name'])]})
    scheduler = LsfJobControl(config(tmp_path), runner=runner)
    scheduler._mode = 'json'
    with pytest.raises(ProtocolError):
        scheduler.cancel(row)
    with pytest.raises(ProtocolError):
        scheduler.cancel(dict(row, cluster='another'))
    assert not any(call[0] == '/fake/bkill' for call in calls)


@pytest.mark.parametrize('raw', ['42|user|RUN', '42|user|BAD|sico-a',
                                '0|user|RUN|sico-a', '42|user|RUN|sico-a|extra'])
def test_invalid_scheduler_rows_are_protocol_damage(raw):
    with pytest.raises(ProtocolError):
        jobs(raw, 'fields')


def test_submit_ack_history_and_unknown_are_distinct():
    assert submitted('Job <42> is submitted to queue <short>.\n') == '42'
    with pytest.raises(ProtocolError):
        submitted('connection lost')
    expected = dict(job_id='42', name='sico-abcd', owner='test', cluster='eda-test')
    raw = 'Job <42>, Job Na\n                     me <sico-abcd>, User <test>\n Done successfully.'
    assert history(raw, expected)['state'] == 'DONE'
    running = raw.replace('Done successfully.', 'Still running.')
    assert history(running, expected)['state'] == 'UNKWN'
    with pytest.raises(ProtocolError):
        history(raw.replace('sico-abcd', 'unrelated'), expected)


def test_submission_quotes_only_captured_site_argv(tmp_path):
    import shlex

    captured = []
    def runner(argv, **kwargs):
        captured.append(argv)
        return (0, 'My cluster name is eda-test\n') if argv[0].endswith('lsid') else (
            0, 'Job <42> is submitted to queue <short>.')
    c = replace(config(tmp_path), node_command=('/site/runtime with spaces/bin/sico',))
    manifest = tmp_path / "shared 'path'" / 'manifest.json'
    scheduler = LsfScheduler(c, runner=runner)
    assert scheduler.submit('sico-fixed', manifest) == '42'
    assert shlex.split(captured[-1][-1]) == [*c.node_command, 'background-node',
                                           '--connection-file', str(manifest)]
    assert '-W' in captured[-1] and '-n' in captured[-1]


def test_command_deadline_and_output_budget_are_bounded():
    env = command_environment(dict(os.environ, SICO_API_KEY='secret', OTHER_SECRET='secret'))
    assert 'SICO_API_KEY' not in env and 'OTHER_SECRET' not in env
    start = time.monotonic()
    with pytest.raises(TimeoutError):
        run_command([sys.executable, '-c', 'import time;time.sleep(30)'],
                    environment=env, timeout=.1)
    assert time.monotonic() - start < 2
    with pytest.raises(ValueError, match='budget'):
        run_command([sys.executable, '-c', 'print("x" * 1100000)'], environment=env, timeout=3)
