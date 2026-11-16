"""Self-contained node execution over real processes and isolated simulated OA peer."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from agent_lsf_fixtures import Scheduler, backend, config, request
from agent_lsf_fixtures import runtime as runtime  # noqa: F401
from test_agent_b2_jobs import status, submit

from sico.service.background_environment import capture_environment
from sico.service.background_jobs import BackgroundJobs
from sico.service.lsf_bundle import prepare_bundle, read_bundle, run_node
from sico.transport.framing import ProtocolError


def test_real_node_survives_manager_close_and_result_is_verified_after_restart(tmp_path, runtime,
                                                                           monkeypatch):
    c = config(tmp_path)
    scheduler = Scheduler(c, node=True)
    adapter, _ = backend(tmp_path, runtime, scheduler)
    jobs = BackgroundJobs(adapter.root, None, backends={'lsf': adapter}, execution_backend='lsf')
    row = submit(jobs)
    try:
        assert scheduler.submitted.wait(3)
        manifest = scheduler.submissions[0][1]
        runtime.context.unlink()  # The submit-host input is no longer visible to the node.
        jobs.close()
        assert jobs.wait(3)
        assert not scheduler.cancellations
        assert scheduler.child.wait(10) == 0, (manifest.parent / 'test-node.log').read_text()
        from sico.service import lsf_evidence

        monkeypatch.setattr(lsf_evidence, 'LsfScheduler', lambda captured: scheduler)
        reopened = BackgroundJobs(adapter.root, None)
        try:
            done = status(reopened, row['job_id'])
            assert done['state'] == 'completed' and done['scheduler_verified']
            assert not reopened.busy
            result = reopened.read_background_result(
                row['job_id'], done['artifact'], session_id='s')
            assert result['inspection']['cellview']['cell'] == 'top'
            assert run_node(manifest) == 4  # Requeue cannot replay an already-started node.
            artifact = manifest.parent / 'workers' / row['job_id'] / done['artifact']['path']
            artifact.write_bytes(b'changed')
            with pytest.raises((ValueError, ProtocolError)):
                reopened.read_background_result(row['job_id'], done['artifact'], session_id='s')
        finally:
            reopened.close()
            assert reopened.wait(3)
    finally:
        jobs.close()
        jobs.wait(3)
        scheduler.close()


def test_bundle_captures_eda_values_without_model_key_or_local_temp(tmp_path, runtime):
    c = config(tmp_path)
    env = capture_environment(dict(PATH='/usr/bin', SICO_VIRTUOSO_LD_LIBRARY_PATH='/eda/lib',
        SICO_VIRTUOSO_PYTHONPATH='/eda/python', SICO_VIRTUOSO_LOADEDMODULES='IC231',
        SICO_API_KEY='secret', CUSTOM_MODEL_KEY='secret', TMPDIR='/submit/private'),
        credential='CUSTOM_MODEL_KEY')
    manifest = prepare_bundle(tmp_path / 'background', request(environment=env),
                              runtime, env, {}, c)
    row, _, _ = read_bundle(manifest)
    assert row['environment']['LD_LIBRARY_PATH'] == '/eda/lib'
    assert row['environment']['PYTHONPATH'] == '/eda/python'
    assert row['environment']['LOADEDMODULES'] == 'IC231'
    text = manifest.read_text()
    assert 'secret' not in text and '/submit/private' not in text
    assert 'bridge' not in row and 'token' not in row
    assert (manifest.stat().st_mode & 0o777) == 0o600
    # Manifest tampering must fail before starting a node process.
    raw = json.loads(text)
    raw['environment']['CUSTOM_MODEL_KEY'] = 'secret'
    manifest.write_text(json.dumps(raw))
    with pytest.raises(ProtocolError):
        read_bundle(manifest)


@pytest.mark.parametrize('damage', ['context', 'symlink', 'outside'])
def test_node_input_damage_and_unshared_library_refuse(tmp_path, runtime, damage):
    c = config(tmp_path)
    if damage == 'outside':
        runtime = replace(runtime, libraries={'Work': Path('/not-shared/library')})
        with pytest.raises(ValueError):
            prepare_bundle(tmp_path / 'background', request(), runtime, {}, {}, c)
        return
    manifest = prepare_bundle(tmp_path / 'background', request(), runtime, {}, {}, c)
    context = manifest.parent / 'worker.cxt'
    if damage == 'context':
        context.write_bytes(b'corrupt')
    else:
        context.unlink()
        context.symlink_to(runtime.context)
    with pytest.raises((ValueError, OSError)):
        read_bundle(manifest)


def test_completed_node_rejects_scheduler_id_and_manifest_substitution(tmp_path, runtime):
    from sico.service.lsf_bundle import verified_receipt
    from sico.service.lsf_evidence import load

    scheduler = Scheduler(config(tmp_path), node=True)
    adapter, _ = backend(tmp_path, runtime, scheduler)
    r = request()
    try:
        done = adapter.create(r, lambda row: None).run()
        assert done['state'] == 'completed'
        with pytest.raises(ProtocolError, match='allocation'):
            verified_receipt(adapter.root, done, dict(done['scheduler'], job_id='99'))
        manifest = scheduler.submissions[0][1]
        value = json.loads(manifest.read_text())
        value['params']['max_items'] = 1
        manifest.write_text(json.dumps(value))
        with pytest.raises(ProtocolError, match='manifest changed'):
            load(adapter.root, done)
    finally:
        scheduler.close()
