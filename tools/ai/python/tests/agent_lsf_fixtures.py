"""Deterministic scheduler and real node-process fixture, without contacting site LSF."""

import os
import pwd
import subprocess
import sys
import threading
from dataclasses import replace
from pathlib import Path

from test_agent_b1_worker import runtime as runtime  # noqa: F401
from test_agent_b2_jobs import origin

from sico.service.execution_backend import ExecutionRequest
from sico.service.lsf_config import LsfConfig


def config(tmp_path, **values):
    defaults = dict(cluster='eda-test', owner=pwd.getpwuid(os.getuid()).pw_name,
        queue='test', shared_root=str(tmp_path),
        node_command=(sys.executable, str(Path(__file__).resolve().parents[3] / "sico/python/sico-entry")),
        bsub='/fake/bsub', bjobs='/fake/bjobs', bkill='/fake/bkill', bhist='/fake/bhist',
        lsid='/fake/lsid', poll_seconds=.02)
    defaults.update(values)
    return LsfConfig(**defaults)


def request(key='a' * 32, environment=None):
    return ExecutionRequest(key, 'b' * 32, 's', origin(), 'inspect_schematic',
                            dict(lib='Work', cell='top', view='schematic'), environment)


class Scheduler:
    def __init__(self, config, *, state='RUN', lost=False, node=False):
        self.config, self.state, self.lost, self.node = config, state, lost, node
        self.submissions, self.cancellations = [], []
        self.child = None
        self.submitted = threading.Event()
        self.fail_query = False
        self.node_output = None

    def submit(self, name, manifest):
        self.submissions.append((name, manifest))
        if self.node:
            env = dict(os.environ, LSF_CLUSTER_NAME=self.config.cluster,
                       LSB_JOBID='42', LSB_JOBNAME=name, SICO_API_KEY='not-for-worker')
            self.node_output = (manifest.parent / 'test-node.log').open('w')
            self.child = subprocess.Popen([*self.config.node_command, 'background-node',
                '--connection-file', str(manifest)], env=env, stdout=self.node_output,
                stderr=subprocess.STDOUT)
        self.submitted.set()
        if self.lost:
            raise TimeoutError('lost bsub acknowledgement')
        return '42'

    def observe(self, expected):
        if self.fail_query:
            raise OSError('temporarily unavailable')
        state = self.state
        if self.child is not None and self.child.poll() is not None:
            state = 'DONE' if self.child.returncode == 0 else 'EXIT'
        return dict(expected, job_id='42', state=state)

    def cancel(self, expected):
        self.cancellations.append(expected)
        return 'requested'  # Deliberately keeps running after the acknowledgement.

    def close(self):
        if self.child is not None:
            if self.child.poll() is None:
                self.child.terminate()
            self.child.wait(timeout=5)
        if self.node_output:
            self.node_output.close()


def backend(tmp_path, runtime, scheduler=None, **kwargs):
    from sico.service.lsf_execution import LsfExecutionBackend

    scheduler = scheduler or Scheduler(config(tmp_path, **kwargs))
    runtime = replace(runtime)
    result = LsfExecutionBackend(tmp_path / 'background', runtime, scheduler.config,
        scheduler=scheduler, worker_options=dict(startup_timeout=3, execution_timeout=3,
                                                 shutdown_timeout=.2))
    return result, scheduler
