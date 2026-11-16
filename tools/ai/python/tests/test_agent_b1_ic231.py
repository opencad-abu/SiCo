"""Licensed B1 acceptance with B0's isolated synthetic OA libraries."""
import hashlib
import json
import os
import subprocess
import sys
import shlex
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from test_agent_b0_background import CAD, IC, SOURCES, TARGET, design, environment, fingerprints
from test_agent_b1_worker import assert_reaped


@pytest.fixture(scope='module')
def compiled(design, tmp_path_factory):
    from cadai.skill_check import check_source
    from sico.service.background_worker import BackgroundRuntime
    root = tmp_path_factory.mktemp('b1-context')
    context = root / 'cadB1.cxt'
    smoke = root / 'smoke.il'
    smoke.write_text('unless(aiBackgroundWorkerRevision()=="20260915.ai.background.worker.v1" '
                     'error("Missing B1 entry"))\n')
    command = [sys.executable, str(CAD.parent / 'deploy/build_skill_context.py'), str(context)]
    for name in (*SOURCES, 'AI_background_worker.il'):
        source = CAD / 'ai/skill' / name
        assert check_source(source.read_bytes(), source_name=name)['ok']
        command += ['--source', str(source)]
    command += ['--probe', str(smoke), '--expected-version', (design / 'passed').read_text(),
                '--virtuoso', str(IC), '--timeout', '60']
    result = subprocess.run(command, cwd=root, env=environment(root),
                            capture_output=True, text=True, timeout=150)
    assert result.returncode == 0, result.stdout + result.stderr
    return BackgroundRuntime(IC, context, hashlib.sha256(context.read_bytes()).hexdigest(),
                             (design / 'passed').read_text(),
                             {'B0Work': design / 'work', 'B0Devices': design / 'devices',
                              'B0Tech': design / 'tech'})


def test_real_workers_have_independent_bridge_and_retained_result(design, compiled, tmp_path):
    from sico.service.background_worker import BackgroundWorker, read_background_artifact
    before = fingerprints(design)
    workers = [BackgroundWorker(tmp_path, compiled, execution_timeout=10) for _ in range(2)]
    with ThreadPoolExecutor(2) as pool:
        receipts = list(pool.map(lambda w: w.run('inspect_schematic', TARGET), workers))
    for w, receipt in zip(workers, receipts):
        assert receipt['state'] == 'completed', (receipt, (w.directory / 'logs/stdout.log').read_text(),
                                                (w.directory / 'logs/stderr.log').read_text())
        assert_reaped(receipt)
        artifact = read_background_artifact(w.directory)
        assert artifact['inspection']['instances'][0]['terminals'][0]['net'] == 'SAVED'
        assert not (w.directory / 'callback-executed').exists()
        assert '*Error*' not in (w.directory / 'logs/stdout.log').read_text()
        assert all(p.stat().st_mode & 0o077 == 0 for p in w.directory.rglob('*') if p.is_file())
    for key in ('worker_id', 'job_id', 'instance_id', 'generation', 'router_id', 'bridge_id', 'pid', 'port'):
        assert receipts[0][key] != receipts[1][key]
    assert fingerprints(design) == before


@pytest.mark.parametrize('failure', ['version', 'missing_tech', 'missing_cell'])
def test_real_worker_unavailable_does_not_publish_artifact(design, compiled, tmp_path, failure):
    from sico.service.background_worker import BackgroundWorker
    before = fingerprints(design)
    target = dict(TARGET)
    if failure == 'version':
        compiled = replace(compiled, version='unsupported-version')
    elif failure == 'missing_tech':
        compiled = replace(compiled, libraries={k: v for k, v in compiled.libraries.items() if k != 'B0Tech'})
    else:
        target['cell'] = 'absent'
    w = BackgroundWorker(tmp_path, compiled, startup_timeout=10, execution_timeout=10)
    receipt = w.run('inspect_schematic', target)
    assert receipt['state'] == 'background_unavailable', receipt
    assert_reaped(receipt)
    assert not receipt.get('artifact')
    assert fingerprints(design) == before


def test_real_worker_runs_with_skill_sources_and_build_tree_hidden(design, compiled, tmp_path):
    from sico.service.background_worker import BackgroundWorker, read_background_artifact
    if not shutil.which('bwrap'):
        pytest.skip('SKILL source isolation requires bwrap')
    wrapper = tmp_path / 'isolated-virtuoso'
    command = ['bwrap', '--ro-bind', '/', '/', '--bind', '/tmp', '/tmp',
               '--dev-bind', '/dev', '/dev', '--proc', '/proc',
               '--tmpfs', str(CAD / 'ai/skill'), '--tmpfs', str(compiled.context.parent), str(IC)]
    wrapper.write_text('#!/bin/sh\nexec ' + shlex.join(command) + ' "$@"\n')
    wrapper.chmod(0o700)
    w = BackgroundWorker(tmp_path, replace(compiled, virtuoso=wrapper), execution_timeout=10)
    receipt = w.run('inspect_schematic', TARGET)
    assert receipt['state'] == 'completed', receipt
    assert read_background_artifact(w.directory)['inspection']['instances']
    assert_reaped(receipt)


@pytest.mark.skipif(not os.environ.get('DISPLAY'), reason='Foreground needs DISPLAY')
def test_background_finishes_while_foreground_skill_is_busy(design, compiled, tmp_path):
    from sico.service.background_worker import BackgroundWorker
    before = fingerprints(design)
    foreground = tmp_path / 'foreground'
    foreground.mkdir(mode=0o700)
    source = foreground / 'foreground.il'
    source.write_text('\n'.join([
        'b1Window=geOpen(?lib "B0Work" ?cell "top" ?view "schematic" ?mode "a")',
        'b1Cv=geGetWindowCellView(b1Window) dbCreateNet(b1Cv "UNSAVED")',
        'b1Port=outfile("busy") fprintf(b1Port "ready") close(b1Port)',
        # Hold the actual SKILL interpreter until the independent worker completes.
        'while(!isFile("release") ipcSleep(1))',
        'unless(and(dbIsCellViewModified(b1Cv) dbFindNetByName(b1Cv "UNSAVED")) error("Foreground changed"))',
        'dbPurge(b1Cv)',
        'b1Port=outfile("done") fprintf(b1Port "ok") close(b1Port) exit(0)',
    ]) + '\n')
    with (foreground / 'stdout.log').open('w') as log:
        process = subprocess.Popen([str(IC), '-nocdsinit', '-cdslib', str(design / 'cds.lib'),
            '-restore', str(source), '-log', str(foreground / 'CDS.log')], cwd=foreground,
            env=environment(foreground, graphical=True), stdout=log, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, start_new_session=True)
        try:
            deadline = time.monotonic() + 20
            while not (foreground / 'busy').exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(.05)
            assert (foreground / 'busy').exists()
            w = BackgroundWorker(tmp_path, compiled, execution_timeout=10)
            receipt = w.run('inspect_schematic', TARGET)
            assert receipt['state'] == 'completed', receipt
            assert process.poll() is None
            assert fingerprints(design) == before
            assert_reaped(receipt)
            (foreground / 'release').touch()
            assert process.wait(timeout=10) == 0
            assert (foreground / 'done').read_text() == 'ok'
        finally:
            if process.poll() is None:
                (foreground / 'release').touch()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
    assert fingerprints(design) == before
