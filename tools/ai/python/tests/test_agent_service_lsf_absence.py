"""Actual LSF 10.1 empty-query text must remain unknown, never an invented terminal."""

import pytest
from agent_lsf_fixtures import config

from sico.lsf.job_control import LsfJobControl
from sico.transport.framing import ProtocolError


@pytest.mark.parametrize('code', [0, 255])
@pytest.mark.parametrize('known', [False, True])
def test_named_absence_queries_history_or_retains_unknown(tmp_path, code, known):
    calls = []
    c = config(tmp_path)
    name = 'sico-' + 'a' * 32
    expected = dict(cluster=c.cluster, owner=c.owner, name=name,
                    job_id='42' if known else None, state='RUN')
    def runner(argv, **kwargs):
        calls.append(argv)
        if argv[0] == c.lsid:
            return 0, 'My cluster name is eda-test\n'
        if argv[0] == c.bhist:
            return code, 'Job <42> is not found\n'
        if '-json' in argv:
            return 255, 'illegal option -- j'
        return code, f'Job <{name}> is not found\n'
    scheduler = LsfJobControl(c, runner=runner)
    assert scheduler.observe(expected) == dict(expected, state='UNKWN')
    assert any(call[0] == c.bhist for call in calls) is known
    assert scheduler.cancel(expected) == 'unconfirmed'
    assert not any(call[0] == c.bkill for call in calls)


def test_absence_falls_back_to_original_terminal_history(tmp_path):
    c = config(tmp_path)
    expected = dict(cluster=c.cluster, owner=c.owner, name='sico-original', job_id='42')
    def runner(argv, **kwargs):
        if argv[0] == c.lsid:
            return 0, 'My cluster name is eda-test\n'
        if argv[0] == c.bhist:
            return 0, f'Job <42>, Job Name <sico-original>, User <{c.owner}>\nDone successfully.'
        return 0, 'Job <sico-original> is not found\n'
    scheduler = LsfJobControl(c, runner=runner)
    scheduler._mode = 'fields'
    assert scheduler.observe(expected)['state'] == 'DONE'


@pytest.mark.parametrize('code', [0, 255])
def test_missing_history_never_invents_a_terminal(tmp_path, code):
    c = config(tmp_path)
    expected = dict(cluster=c.cluster, owner=c.owner, name='sico-original',
                    job_id='42', state='RUN')
    def runner(argv, **kwargs):
        if argv[0] == c.lsid:
            return 0, 'My cluster name is eda-test\n'
        if argv[0] == c.bhist:
            return code, 'No matching job found\n'
        return 0, 'Job <sico-original> is not found\n'
    scheduler = LsfJobControl(c, runner=runner)
    scheduler._mode = 'fields'
    assert scheduler.observe(expected) == dict(expected, state='UNKWN')


@pytest.mark.parametrize('reply', ['Job <other> is not found',
                                 'Job <sico-original> is not found\n42|user|RUN|sico-original'])
def test_wrong_identity_or_mixed_missing_reply_is_protocol_damage(tmp_path, reply):
    c = config(tmp_path)
    def runner(argv, **kwargs):
        return ((0, 'My cluster name is eda-test\n') if argv[0] == c.lsid else (0, reply))
    scheduler = LsfJobControl(c, runner=runner)
    scheduler._mode = 'fields'
    with pytest.raises(ProtocolError):
        scheduler.observe(dict(cluster=c.cluster, owner=c.owner, name='sico-original', job_id=None))


@pytest.mark.parametrize('code', [0, 255])
def test_json_probe_can_report_no_job_without_json_records(tmp_path, code):
    c = config(tmp_path)
    def runner(argv, **kwargs):
        return ((0, 'My cluster name is eda-test\n') if argv[0] == c.lsid else
                (code, 'Job <sico-probe-no-job> is not found\n'))
    assert LsfJobControl(c, runner=runner).probe() == 'json'
