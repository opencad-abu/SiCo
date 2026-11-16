"""Offline validation of the opt-in live harness, including spawned phase owners."""

import json
import os
import pwd
from dataclasses import asdict
from pathlib import Path

import pytest
from agent_lsf_fixtures import config
from agent_lsf_fixtures import runtime as runtime  # noqa: F401
from agent_lsf_live_run import create_run, phase, verify_lock
from agent_lsf_live_site import CONTRACT, load_site

from sico.storage.project_files import read_record
from sico.transport.framing import ProtocolError


def site_file(tmp_path, runtime):
    c = config(tmp_path, **{name: '/bin/true'
                          for name in ('bsub', 'bjobs', 'bkill', 'bhist', 'lsid')})
    execution = tmp_path / 'execution.json'
    execution.write_text(json.dumps(dict(protocol='cadai.background.execution.v1',
        runtime=asdict(runtime), lsf=asdict(c)), default=str))
    execution.chmod(0o600)
    shared = tmp_path / 'approved'
    shared.mkdir(mode=0o700)
    alias = tmp_path / 'alias'
    alias.symlink_to(shared, target_is_directory=True)
    path = tmp_path / 'site.json'
    path.write_text(json.dumps(dict(protocol=CONTRACT, execution_config=str(execution),
        shared_tests=str(shared), alias_tests=str(alias),
        target=dict(lib='Work', cell='top', view='schematic'), environment={},
        wait_seconds=5, max_slots=1, max_wall_minutes=5)))
    path.chmod(0o600)
    return path


@pytest.mark.parametrize('damage', ['resource', 'alias', 'credential', 'target', 'relative'])
def test_live_site_rejects_unapproved_or_unbounded_inputs(tmp_path, runtime, damage):
    path = site_file(tmp_path, runtime)
    value = json.loads(path.read_text())
    if damage == 'resource':
        execution = Path(value['execution_config'])
        row = json.loads(execution.read_text())
        row['lsf']['slots'] = 2
        execution.write_text(json.dumps(row))
    elif damage == 'alias':
        value['alias_tests'] = str(tmp_path)
    elif damage == 'credential':
        value['environment']['SICO_API_KEY'] = 'must-not-persist'
    elif damage == 'target':
        value['target']['lib'] = 'NotApproved'
    else:
        value['execution_config'] = 'relative.json'
    path.write_text(json.dumps(value))
    with pytest.raises(ProtocolError):
        load_site(path)
    assert list((tmp_path / 'approved').iterdir()) == []


def test_site_capture_and_shared_alias_lock_use_real_competing_process(tmp_path, runtime):
    site = load_site(site_file(tmp_path, runtime))
    root = create_run(site, 'nas_lock')
    row = verify_lock(site, root)
    assert row['outcome'] == 'passed' and row['alias_exercised']
    assert row['cross_node_verified'] is False
    assert root.stat().st_mode & 0o777 == 0o700
    assert (root / 'run.json').stat().st_mode & 0o777 == 0o600


def test_default_live_gate_skips_before_reading_any_site_input(monkeypatch):
    import test_agent_service_lsf_live as live

    monkeypatch.delenv('SICO_AS06_LIVE', raising=False)
    monkeypatch.setenv('SICO_AS06_SITE', '/must-not-read')
    monkeypatch.setattr(live, 'load_site', lambda _: pytest.fail('Unexpected site access'))
    with pytest.raises(pytest.skip.Exception):
        live.live_site.__wrapped__()


def scheduler_scripts(tmp_path, site_path):
    """Subprocess fake scheduler exercises harness transport, without site LSF or EDA."""
    import sys

    script = tmp_path / 'scheduler'
    script.write_text('#!' + sys.executable + '\n' + '''
import json, sys
from pathlib import Path
root = Path(__file__).parent
store = root / 'jobs.json'
args = sys.argv[1:]
tool = Path(sys.argv[0]).name
jobs = json.loads(store.read_text()) if store.exists() else {}
if tool == 'lsid':
    print('My cluster name is eda-test')
elif tool == 'bsub':
    name = args[args.index('-J') + 1]
    jobs[name] = 'RUN'
    store.write_text(json.dumps(jobs))
    with (root / 'submitted').open('a') as f: f.write(name + '\\n')
    print('Job <42> is submitted to queue <test>.')
elif tool == 'bkill':
    name = args[args.index('-J') + 1]
    jobs[name] = 'EXIT'
    store.write_text(json.dumps(jobs))
    print('Job <42> is being terminated')
elif tool == 'bjobs':
    name = args[args.index('-J') + 1]
    import os, pwd
    owner = pwd.getpwuid(os.getuid()).pw_name
    rows = [dict(JOBID='42', USER=owner, STAT=jobs[name], JOB_NAME=name)] if name in jobs else []
    print(json.dumps(dict(RECORDS=rows)))
else:
    raise SystemExit(1)
''')
    script.chmod(0o700)
    path = Path(json.loads(site_path.read_text())['execution_config'])
    value = json.loads(path.read_text())
    for name in ('lsid', 'bsub', 'bjobs', 'bkill', 'bhist'):
        command = tmp_path / name
        command.symlink_to(script)
        value['lsf'][name] = str(command)
    path.write_text(json.dumps(value))


def test_real_phase_owner_exit_cancel_and_unknown_result_remain_distinct(tmp_path, runtime):
    path = site_file(tmp_path, runtime)
    scheduler_scripts(tmp_path, path)
    site = load_site(path)
    root = create_run(site, 'cancel')
    accepted = phase(site, root, 'submit')
    assert accepted['outcome'] == 'observed', accepted
    assert accepted['row']['scheduler']['owner'] == pwd.getpwuid(os.getuid()).pw_name
    cancelled = phase(site, root, 'cancel')
    assert cancelled['outcome'] == 'passed', cancelled
    assert cancelled['pending'] and cancelled['external_outcome'] == 'unconfirmed'
    observed = phase(site, root, 'observe')
    assert observed['outcome'] == 'needs_reconcile'  # EXIT alone cannot supply an artifact.
    with pytest.raises(ValueError, match='original evidence'):
        phase(site, root, 'submit')
    assert len((tmp_path / 'submitted').read_text().splitlines()) == 1
    assert read_record(root / 'run.json')['excluded'] == [
        'gui_detach', 'project_service_restart', 'cross_node_nas_lock',
        'service_allocation_termination']


def test_phase_owner_loss_retains_admission_without_automatic_cancel(tmp_path, runtime,
                                                                    monkeypatch):
    import agent_lsf_live_run as run

    site = load_site(site_file(tmp_path, runtime))
    root = create_run(site, 'detach_reassociate')
    monkeypatch.setattr(run, '_child', lambda *args: False)
    row = phase(site, root, 'submit')
    assert row['outcome'] == 'needs_reconcile' and row['owner_exit'] == 'unconfirmed'
    assert read_record(root / 'run.json')['job_id']
    assert not (root / 'background').exists()
    assert set(root.glob('*json')) == {root / 'run.json', root / 'submit.json'}


def test_successful_artifact_is_required_for_observation_pass(tmp_path, runtime, monkeypatch):
    import agent_lsf_live_phases as phases
    from agent_lsf_fixtures import Scheduler

    from sico.service.lsf_config import lsf_settings
    from sico.service.lsf_execution import LsfExecutionBackend

    site = load_site(site_file(tmp_path, runtime))
    root = create_run(site, 'detach_reassociate')
    scheduler = Scheduler(lsf_settings(site.execution['lsf']), node=True)
    adapter = LsfExecutionBackend(root / 'background', runtime, scheduler.config,
                                  scheduler=scheduler)
    monkeypatch.setattr(phases, 'captured_backend', lambda *_: adapter)
    try:
        phases.run_phase(site, root, 'submit')
        accepted = read_record(root / 'submit.json')
        assert accepted['outcome'] == 'observed', accepted
        assert scheduler.child.wait(10) == 0
        phases.run_phase(site, root, 'observe')
        done = read_record(root / 'observe.json')
        assert done['outcome'] == 'passed', done
        assert not done['pending'] and done['target'] == site.target
        assert len(scheduler.submissions) == 1
    finally:
        scheduler.close()
