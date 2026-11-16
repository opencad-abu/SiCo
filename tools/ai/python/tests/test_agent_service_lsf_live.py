"""Opt-in real LSF/NAS checks using existing runtimes; no compilation fixtures."""

import os

import pytest
from agent_lsf_live_run import create_run, phase, verify_lock
from agent_lsf_live_site import load_site


@pytest.fixture
def live_site():
    if os.environ.get('SICO_AS06_LIVE') != '1':
        pytest.skip('AS-06D requires explicit opt-in and approved site inputs')
    path = os.environ.get('SICO_AS06_SITE')
    if not path:
        pytest.fail('SICO_AS06_SITE must name the approved private site JSON')
    return load_site(path)


@pytest.fixture
def nas_site(request):
    if os.environ.get('SICO_AS06_NAS') != '1':
        pytest.skip('NAS filesystem acceptance deferred by user on 2026-09-21')
    return request.getfixturevalue('live_site')


def exercised(row, root):
    if row['outcome'] == 'not_exercised':
        pytest.skip('Timing did not exercise this case; retain evidence at ' + str(root))
    assert row['outcome'] in {'passed', 'observed'}, str(root) + ': ' + repr(row)


def test_live_shared_directory_lock_and_alias(nas_site):
    root = create_run(nas_site, 'nas_lock')
    row = verify_lock(nas_site, root)
    assert row['outcome'] == 'passed'
    if not row['alias_exercised']:
        pytest.skip('Local shared-path lock passed; alias input absent: ' + str(root))


def test_live_backend_owner_exit_and_original_job_reassociation(live_site):
    root = create_run(live_site, 'detach_reassociate')
    exercised(phase(live_site, root, 'submit'), root)
    row = phase(live_site, root, 'observe')
    exercised(row, root)
    assert row['pending'] is False and row['target'] == live_site.target


def test_live_cancel_requests_original_job_and_observes_terminal(live_site):
    root = create_run(live_site, 'cancel')
    exercised(phase(live_site, root, 'submit'), root)
    row = phase(live_site, root, 'cancel')
    exercised(row, root)
    assert row['row']['scheduler']['state'] == 'EXIT'
    assert row['external_outcome'] == 'unconfirmed'
