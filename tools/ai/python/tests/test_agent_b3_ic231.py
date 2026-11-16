"""Fault injection stops only newly created B3 synthetic Virtuoso workers."""
import json
import os
import signal
from dataclasses import replace

import pytest
from sico.service.background_worker import BackgroundWorker, read_background_artifact
from test_agent_b0_background import IC, fingerprints, run
from test_agent_b0_background import design as design
from test_agent_b1_ic231 import compiled as compiled
from test_agent_b1_worker import assert_reaped

B3_TARGET = dict(lib='SicoTest', cell='top', view_name='schematic',
                 oa_view_type='schematic')


@pytest.fixture(scope='module')
def sicotest(design):
    """Create the auxiliary B3 target only in the isolated SicoTest library."""
    run(design, [
        f'ddCreateLib("SicoTest" {json.dumps(str(design / "sicotest"))})',
        'techBindTechFile(ddGetObj("SicoTest") "B0Tech")',
        'b3Master=dbOpenCellViewByType("B0Devices" "leaf" "symbol" "" "r")',
        'b3Top=dbOpenCellViewByType("SicoTest" "top" "schematic" "schematic" "w")',
        'b3Inst=dbCreateInst(b3Top b3Master "I0" 0:0 "R0")',
        'b3Net=dbCreateNet(b3Top "SAVED")',
        'dbCreateInstTerm(b3Net b3Inst car(b3Master~>terminals))',
        'dbCreateTerm(b3Net "SAVED" "inputOutput")',
        'b3Pin=dbCreateRect(b3Top list("B0Geometry" "drawing") list(2:0 2.1:0.1))',
        'dbCreatePin(b3Net b3Pin "SAVED")',
        'dbSave(b3Top) dbClose(b3Top) dbClose(b3Master)',
    ], cds_lib=design / 'cds.lib', binary=IC)
    return design / 'sicotest'


@pytest.mark.parametrize('fault', ['frozen_worker', 'guardian_exit', 'custodian_exit'])
def test_native_watchdog_fault_reclaims_worker_and_next_job_is_independent(
        design, compiled, sicotest, tmp_path, fault):
    before = fingerprints(design)
    runtime = replace(compiled, libraries={**compiled.libraries, 'SicoTest': sicotest})
    holder = {}
    def inject(receipt):
        if receipt['state'] == 'running' and not receipt.get('request_id'):
            worker = holder['worker']
            # Health already verified this exact PID in the newly created process group.
            os.kill(receipt['pid'], signal.SIGSTOP)
            if fault == 'guardian_exit':
                watcher = json.loads((worker.directory / 'watcher.json').read_text())
                os.kill(watcher['guardian']['pid'], signal.SIGKILL)
            elif fault == 'custodian_exit':
                os.kill(worker.guardian.pid, signal.SIGKILL)
    worker = BackgroundWorker(tmp_path, runtime, execution_timeout=1,
                              shutdown_timeout=.2, progress=inject)
    holder['worker'] = worker
    receipt = worker.run('inspect_schematic', B3_TARGET)
    assert receipt['state'] == 'worker_timeout', receipt
    assert_reaped(receipt)
    assert not receipt.get('artifact')
    assert (worker.directory / 'logs/stdout.log').exists()
    assert (worker.directory / 'router/requests.jsonl').exists()
    if fault == 'frozen_worker':
        report = json.loads((worker.directory / 'watchdog.json').read_text())
        assert report['reason'] == 'execution_timeout' and report['process_stopped']
    elif fault == 'guardian_exit':
        custody = json.loads((worker.directory / 'custody.json').read_text())
        assert custody['phase'] == 'finished' and custody['tree_owned']
        assert custody['process_stopped'] and custody['children_remaining'] == 0
    following = BackgroundWorker(tmp_path, runtime, execution_timeout=10, shutdown_timeout=.5)
    completed = following.run('inspect_schematic', B3_TARGET)
    assert completed['state'] == 'completed', completed
    assert_reaped(completed)
    assert read_background_artifact(following.directory)['inspection']['instances']
    for key in ('job_id', 'worker_id', 'generation', 'bridge_id', 'router_id'):
        assert completed[key] != receipt[key]
    assert fingerprints(design) == before
