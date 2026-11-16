"""A failed site run must not look like an optional unexercised timing branch."""

import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from agent_lsf_fixtures import config, request
from agent_lsf_fixtures import runtime as runtime  # noqa: F401
from agent_lsf_live_phases import cancel, finished_before_action, submit

from sico.storage.project_files import write_record
from sico.transport.framing import ProtocolError


class Manager:
    def __init__(self, row, *, result=None, outcome='unconfirmed'):
        self.row, self.result, self.outcome = row, result, outcome
        self.cancels = 0

    def submit_background(self, *args, **kwargs):
        return self.row

    def get_background_status(self, *args, **kwargs):
        return self.row

    def read_background_result(self, *args, **kwargs):
        if self.result is None:
            raise ProtocolError('Artifact is not verified')
        return self.result

    def cancel_background(self, *args, **kwargs):
        self.cancels += 1
        self.row = dict(self.row, state='running_unknown',
                        scheduler=dict(self.row['scheduler'], state='EXIT'))
        return dict(self.row, cancel_outcome=self.outcome)


def site():
    return SimpleNamespace(target=dict(lib='Work', cell='top', view='schematic'),
                           environment={}, wait_seconds=.1,
                           execution={'lsf': {'poll_seconds': .01}})


@pytest.mark.parametrize('state', ['interrupted', 'background_unavailable', 'queue_timeout'])
def test_failed_admission_never_skips_as_timing(tmp_path, state):
    row = submit(Manager(dict(state=state)), 'a' * 32, site(), tmp_path)
    assert row['outcome'] == 'needs_reconcile'


def test_exit_before_cancel_is_failure_not_unexercised(tmp_path):
    manager = Manager(dict(state='running_unknown', scheduler={'state': 'EXIT'}))
    assert cancel(manager, 'a' * 32, site(), tmp_path)['outcome'] == 'needs_reconcile'
    assert not manager.cancels


def test_only_verified_done_can_be_skipped_for_timing():
    config = site()
    row = dict(state='completed', scheduler={'state': 'DONE'}, artifact={})
    manager = Manager(row)
    with pytest.raises(ProtocolError):
        finished_before_action(manager, 'a' * 32, config, row)
    manager.result = dict(inspection=dict(cellview=config.target))
    assert finished_before_action(manager, 'a' * 32, config, row)['outcome'] == 'not_exercised'


@pytest.mark.parametrize('ack,expected', [('unconfirmed', 'needs_reconcile'),
                                        ('requested', 'passed')])
def test_cancel_reply_and_exit_are_separate_facts(tmp_path, ack, expected):
    scheduler = dict(state='RUN', job_id='42', name='sico-original', cluster='eda', owner='test')
    manager = Manager(dict(state='running', scheduler=scheduler), outcome=ack)
    write_record(tmp_path / 'submit.json', dict(row=manager.row))
    row = cancel(manager, 'a' * 32, site(), tmp_path)
    assert row['outcome'] == expected


def test_concurrent_phase_call_cannot_start_two_owners(tmp_path, monkeypatch):
    import agent_lsf_live_run as run

    entered, release = threading.Event(), threading.Event()
    calls = []
    def paused(*args):
        calls.append(args)
        entered.set()
        assert release.wait(5)
        return True
    monkeypatch.setattr(run, '_child', paused)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(run.phase, site(), tmp_path, 'submit')
        try:
            assert entered.wait(3)
            second = pool.submit(run.phase, site(), tmp_path, 'submit')
            with pytest.raises(ValueError, match='original evidence'):
                second.result(3)
        finally:
            release.set()
        assert first.result(3)['outcome'] == 'needs_reconcile'
    assert len(calls) == 1


@pytest.mark.parametrize('cluster', ['eda-test', 'other-cluster'])
def test_node_without_cluster_environment_uses_captured_lsid(tmp_path, runtime,
                                                           monkeypatch, cluster):
    import os
    import pwd

    from sico.service.lsf_bundle import prepare_bundle, run_node
    from sico.service.lsf_evidence import initial, save
    from sico.lsf.job_control import LsfJobControl

    c, r = config(tmp_path), request()
    root = tmp_path / 'background'
    manifest = prepare_bundle(root, r, runtime, {}, {}, c)
    save(root, initial(r, c, manifest))
    monkeypatch.delenv('LSF_CLUSTER_NAME', raising=False)
    monkeypatch.setenv('LSB_JOBNAME', 'sico-' + r.job_id)
    monkeypatch.setenv('LSB_JOBID', '42')
    calls = []
    def verify(self):
        calls.append(self.config.lsid)
        return cluster
    monkeypatch.setattr(LsfJobControl, 'verify_cluster', verify)
    if cluster != c.cluster:
        with pytest.raises(ProtocolError, match='allocation'):
            run_node(manifest)
        assert not (manifest.parent / 'node.started.json').exists()
    else:
        assert c.owner == pwd.getpwuid(os.getuid()).pw_name
        assert run_node(manifest) == 0
    assert calls == [c.lsid]
