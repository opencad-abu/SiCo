"""Session-scoped configuration, authority and lifecycle boundaries for batch work."""

import json
import threading
from contextlib import ExitStack
from dataclasses import asdict

import pytest
from agent_lsf_fixtures import Scheduler, config
from agent_lsf_fixtures import runtime as runtime  # noqa: F401
from agent_service_business_fixtures import business_service, exchange, open_request
from agent_worker_fixtures import heartbeat
from PyQt5.QtWidgets import QLabel
from test_agent_b2_jobs import origin
from test_agent_service_control import attach

from sico.service.background_config import load_settings
from sico.service.lsf_evidence import load
from sico.service.project_sessions import ProjectSessionOwner
from sico.storage.session_snapshot import read
from sico.transport.framing import ProtocolError


def settings(tmp_path, runtime):
    path = tmp_path / 'batch.json'
    path.write_text(json.dumps(dict(protocol='cadai.background.execution.v1',
        runtime=asdict(runtime), lsf=asdict(config(tmp_path))), default=str))
    path.chmod(0o600)
    return path


def test_session_config_is_captured_and_gui_batch_requires_current_control(tmp_path, runtime,
                                                                        monkeypatch):
    from sico.service import lsf_evidence

    path = settings(tmp_path, runtime)
    scheduler = Scheduler(config(tmp_path))
    monkeypatch.setattr(lsf_evidence, 'LsfScheduler', lambda captured: scheduler)
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        result = exchange(service, open_request(context=origin(), environment={
            'SICO_BACKGROUND_CONFIG': str(path), 'CUSTOM_KEY': 'secret',
            'SICO_VIRTUOSO_LOADEDMODULES': 'IC231'}))
        assert result.outcome == 'opened'
        controller = service.owner.controllers['live']
        saved = read(controller.journal.directory)
        assert saved['background']['lsf']['queue'] == 'test'
        path.write_text('changed after session creation')
        api, token = attach(stack, service, tmp_path)
        kwargs = dict(origin=origin())
        with pytest.raises(ValueError):
            api.background_request(token, 'submit_background', 'inspect_schematic',
                                   dict(origin().snapshot['cellview']), **kwargs).result(5)
        api.control.request(token, 'acquire').result(5)
        row = api.background_request(token, 'submit_background', 'inspect_schematic',
                                     dict(origin().snapshot['cellview']), **kwargs).result(5)
        assert row['execution_backend'] == 'lsf'
        assert scheduler.submitted.wait(3)
        manifest = scheduler.submissions[0][1]
        captured = json.loads(manifest.read_text())
        assert captured['environment']['LOADEDMODULES'] == 'IC231'
        assert 'secret' not in manifest.read_text()
        other, other_token = attach(stack, service, tmp_path)
        other.control.request(other_token, 'status').result(5)
        other.control.request(other_token, 'takeover').result(5)
        with pytest.raises(ValueError):
            api.background_request(token, 'cancel_background', row['job_id']).result(5)
        observed = other.background_request(other_token, 'get_background_status', row['job_id'])
        assert observed.result(5)['owner_session_id'] == 'live'
        api.close_desktop()
        assert not scheduler.cancellations
        assert service.dispatch.pending_work().jobs


def test_session_creation_copies_captured_config_after_site_file_changes(tmp_path, runtime):
    path = settings(tmp_path, runtime)
    with ProjectSessionOwner(tmp_path, environment={}) as owner:
        from unittest.mock import patch

        from agent_service_business_fixtures import Bridge, Provider

        with patch('sico.service.project_sessions.provider_from_config',
                   return_value=Provider()):
            owner.open('first', {}, origin(), bridge=Bridge({}),
                       environment={'SICO_BACKGROUND_CONFIG': str(path)})
            captured = owner.dependencies_for('first').background
            path.write_text('no longer valid')
            owner.create_from('first', 'second', origin())
            assert owner.dependencies_for('second').background == captured


def test_slow_scheduler_observation_leaves_qt_and_lifecycle_responsive(tmp_path, runtime,
                                                                   monkeypatch):
    from agent_lsf_fixtures import backend

    from sico.service.background_jobs import BackgroundJobs

    entered, release = threading.Event(), threading.Event()
    scheduler = Scheduler(config(tmp_path))
    adapter, _ = backend(tmp_path, runtime, scheduler)
    jobs = BackgroundJobs(adapter.root, None, backends={'lsf': adapter}, execution_backend='lsf')
    original = scheduler.observe
    def slow(expected):
        entered.set()
        assert release.wait(5)
        return original(expected)
    monkeypatch.setattr(scheduler, 'observe', slow)
    label = QLabel()
    try:
        jobs.submit_background('inspect_schematic', dict(origin().snapshot['cellview']),
                               session_id='s', origin=origin())
        assert entered.wait(3)
        heartbeat(label)
        assert jobs.busy  # Read does not wait for scheduler I/O.
    finally:
        release.set()
        jobs.close()
        assert jobs.wait(3)
        label.close()


def test_invalid_config_and_scheduler_evidence_are_not_definite_refusals(tmp_path, runtime):
    path = settings(tmp_path, runtime)
    row = json.loads(path.read_text())
    row['lsf']['node_command'] = '/shell string'
    path.write_text(json.dumps(row))
    with pytest.raises(ProtocolError):
        load_settings(path)
    from agent_lsf_fixtures import backend

    from sico.service.background_jobs import BackgroundJobs

    scheduler = Scheduler(config(tmp_path))
    adapter, _ = backend(tmp_path, runtime, scheduler)
    jobs = BackgroundJobs(adapter.root, None, backends={'lsf': adapter}, execution_backend='lsf')
    try:
        admitted = jobs.submit_background('inspect_schematic', dict(origin().snapshot['cellview']),
                                          session_id='s', origin=origin())
        assert scheduler.submitted.wait(3)
        jobs.close()
        assert jobs.wait(3)
        evidence = scheduler.submissions[0][1].with_name('scheduler.json')
        value = json.loads(evidence.read_text())
        value['scheduler']['owner'] = 'another'
        evidence.write_text(json.dumps(value))
        with pytest.raises(ProtocolError):
            load(jobs.root, admitted)
    finally:
        jobs.close()
        assert jobs.wait(3)


def test_failed_config_capture_rolls_back_opening_count(tmp_path):
    with ProjectSessionOwner(tmp_path) as owner:
        with pytest.raises(FileNotFoundError):
            owner.open('missing', {}, origin(), environment={
                'SICO_BACKGROUND_CONFIG': str(tmp_path / 'missing.json')})
        assert owner.pending_work().sessions == 0
        assert not owner.controllers


@pytest.mark.parametrize('damage', ['array', 'unknown', 'path', 'budget'])
def test_malformed_site_settings_are_protocol_damage(tmp_path, runtime, damage):
    path = settings(tmp_path, runtime)
    row = json.loads(path.read_text())
    if damage == 'array':
        row = []
    elif damage == 'unknown':
        row['lsf']['extra'] = True
    elif damage == 'path':
        row['lsf']['bsub'] = 'relative'
    else:
        row['lsf']['slots'] = True
    path.write_text(json.dumps(row))
    with pytest.raises(ProtocolError):
        load_settings(path)


def test_project_batch_scan_does_not_reload_process_site_settings(tmp_path, monkeypatch):
    from sico.service.project_workspace import ProjectWorkspace
    from sico.storage.project_files import registry_directory

    registry_directory(tmp_path, create=True)
    monkeypatch.setenv('SICO_BACKGROUND_CONFIG', str(tmp_path / 'now-missing.json'))
    with ProjectSessionOwner(tmp_path, environment={}) as owner:
        workspace = ProjectWorkspace(owner)
        try:
            assert workspace.background.request('list_background', session_id='s').result(3) == []
            assert not workspace.background.busy
        finally:
            assert workspace.close()
