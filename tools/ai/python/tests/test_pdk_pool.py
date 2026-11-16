"""Scheduling, host budget and cleanup contracts without a Cadence license."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

import pytest

from cadai import pdk_pool
from cadai.pdk_pool import PdkPool, claim_slot, collection_scope
from cadai.pdk_schema import PdkUnavailable
from cadai.pdk_worker import DbAccessWorker, settings, stable_device, stop_group
from pdk_fixture import Bridge


@pytest.fixture
def setup_pool(tmp_path):
    context_file = tmp_path / "cadPdkWork.cxt"
    context_file.write_bytes(b"test context")
    env = {"SICO_PDK_WORKER_CONTEXT": str(context_file), "SICO_PDK_DBACCESS": sys.executable,
           "SICO_PDK_WORKERS": "3", "SICO_PDK_WORKER_TIMEOUT": "1"}
    bridge = Bridge()
    bridge.count = 12
    rows = bridge.capture("directory", {})["data"]["items"]
    probe = bridge.capture("device", rows[0]["identity"]["target"])
    return env, bridge, rows, probe


class Reader:
    instances = []
    gate = None
    broken = False

    def __init__(self, root, environment, origin, library, cancel, config, slot):
        self.cancel, self.closed = cancel, False
        self.calls = []
        self.instances.append(self)
        self.bridge = Bridge()
        self.bridge.ctx = origin

    def capture(self, target):
        self.calls.append(target["cell"])
        if len(self.calls) > 1:
            if self.gate:
                while not self.gate.is_set() and not self.cancel.wait(0.01):
                    pass
            elif target["cell"] == "cell0":
                self.cancel.wait(0.08)
        value = self.bridge.capture("device", target)
        if self.broken:
            value["data"]["cdf"]["presence"] = "absent"
        return value

    def close(self):
        self.closed = True


def make_pool(tmp_path, setup_pool, worker=Reader):
    env, bridge, rows, probe = setup_pool
    return PdkPool(tmp_path / "workers", env, bridge.ctx, "synthetic", rows, probe,
                   worker_factory=worker)


def test_persistent_workers_dynamically_schedule_and_complete_out_of_order(tmp_path, setup_pool):
    Reader.instances = []
    pool = make_pool(tmp_path, setup_pool)
    try:
        pool.start()
        done, running = [], []
        deadline = time.monotonic() + 5
        while len(done) < 12:
            assert time.monotonic() < deadline
            for state, cell, value in pool.drain(seconds=0.1):
                (done if state == "done" else running).append(cell)
        assert set(done) == set(running) == {"cell" + str(i) for i in range(12)}
        assert len(done) == len(running) == 12
        assert done.index("cell0") > 0
        assert pool.started == 3
        assert any(len(r.calls) > 3 for r in Reader.instances)
    finally:
        pool.close()
    assert pool.active == 0 and all(r.closed for r in Reader.instances)
    assert not any(t.is_alive() for t in pool.threads)


def test_owning_task_cancels_workers_between_tool_calls(tmp_path, setup_pool):
    class Waiting(Reader):
        instances = []
        gate = threading.Event()
    cancelled = threading.Event()
    with collection_scope(cancelled.is_set):
        pool = make_pool(tmp_path, setup_pool, Waiting)
        pool.start()
    cancelled.set()
    for thread in pool.threads:
        thread.join(timeout=2)
    assert not any(t.is_alive() for t in pool.threads)
    with pytest.raises(PdkUnavailable, match="cancelled"):
        pool.drain()
    pool.close()
    assert all(r.closed for r in Waiting.instances)


def test_worker_mismatch_stops_all_workers_and_preserves_failure(tmp_path, setup_pool):
    class Different(Reader):
        instances = []
        broken = True
    pool = make_pool(tmp_path, setup_pool, Different)
    pool.start()
    for thread in pool.threads:
        thread.join(timeout=2)
    with pytest.raises(PdkUnavailable) as error:
        pool.drain()
    assert error.value.code == "pdk_worker_context_mismatch"
    pool.close()
    assert all(r.closed for r in Different.instances)


def test_launch_failure_releases_host_slot(tmp_path, setup_pool, monkeypatch):
    monkeypatch.setattr(pdk_pool, "MAX_PROCESSES", 1)
    def broken(*args):
        raise OSError("cannot launch")
    pool = make_pool(tmp_path, setup_pool, broken)
    pool.start()
    for thread in pool.threads:
        thread.join(timeout=2)
    with pytest.raises(OSError, match="cannot launch"):
        pool.check()
    pool.close()
    slot = claim_slot(threading.Event(), 1)
    os.close(slot)


def test_host_user_budget_is_32_across_processes(tmp_path):
    cancel = threading.Event()
    slots = []
    try:
        for _ in range(32):
            slots.append(claim_slot(cancel, 1))
        code = """
import os, threading
from cadai.pdk_pool import claim_slot
from cadai.pdk_schema import PdkUnavailable
try:
    fd = claim_slot(threading.Event(), 0.05)
except PdkUnavailable as exc:
    print(exc.code)
else:
    os.close(fd)
    print('acquired')
"""
        tools = Path(__file__).resolve().parents[3]
        paths = (tools / "ai/python", tools / "common/python")
        env = dict(os.environ, PYTHONPATH=os.pathsep.join(map(str, paths)))
        first = subprocess.check_output([sys.executable, "-c", code], env=env, text=True)
        assert first.strip() == "pdk_worker_queue_timeout"
        os.close(slots.pop())
        second = subprocess.check_output([sys.executable, "-c", code], env=env, text=True)
        assert second.strip() == "acquired"
    finally:
        for fd in slots:
            os.close(fd)


def test_stop_kills_term_resistant_descendant_after_leader_exits(tmp_path):
    script = """
import os, signal, time
r, w = os.pipe()
pid = os.fork()
if pid == 0:
    os.close(r)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    os.write(w, b'ready')
    os.close(w)
    while True: time.sleep(1)
os.close(w)
os.read(r, 5)
os.close(r)
print(pid, flush=True)
while True: time.sleep(1)
"""
    process = subprocess.Popen([sys.executable, "-c", script], start_new_session=True,
                               stdout=subprocess.PIPE, text=True)
    child = int(process.stdout.readline())
    try:
        stop_group(process)
        deadline = time.monotonic() + 2
        while True:
            try:
                state = Path(f"/proc/{child}/stat").read_text().rsplit(")", 1)[1].split()[0]
            except FileNotFoundError:
                break
            if state == "Z":
                break
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert process.returncode is not None
    finally:
        stop_group(process)
        process.stdout.close()


@pytest.mark.parametrize("workers", ["0", "33", "eight", "1.5"])
def test_invalid_pool_sizes_are_rejected(setup_pool, workers):
    env, bridge, _, _ = setup_pool
    with pytest.raises(PdkUnavailable) as error:
        settings({**env, "SICO_PDK_WORKERS": workers}, bridge.ctx)
    assert error.value.code == "invalid_pdk_workers"


def test_protected_context_is_required_and_modified_probe_rejected(setup_pool):
    env, bridge, _, probe = setup_pool
    with pytest.raises(PdkUnavailable) as error:
        settings({**env, "SICO_PDK_WORKER_CONTEXT": "/missing/worker.il"}, bridge.ctx)
    assert error.value.code == "pdk_worker_context_unavailable"
    probe["data"]["database"]["master_state"]["modified"] = True
    with pytest.raises(PdkUnavailable, match="saved"):
        stable_device(probe)


def test_stale_response_and_timeout_are_rejected(tmp_path, setup_pool):
    env, bridge, rows, probe = setup_pool
    worker = object.__new__(DbAccessWorker)
    worker.root, worker.cancel, worker.timeout = tmp_path, threading.Event(), 0.02
    worker.process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"],
                                     start_new_session=True)
    try:
        with pytest.raises(PdkUnavailable) as error:
            worker._wait(tmp_path / "missing.json")
        assert error.value.code == "pdk_worker_timeout"
        worker._wait = lambda _: {"request_id": "stale", "ok": True}
        with pytest.raises(PdkUnavailable) as error:
            worker.capture(rows[0]["identity"]["target"])
        assert error.value.code == "invalid_capture"
        assert (tmp_path / "request").stat().st_mode & 0o777 == 0o600
    finally:
        stop_group(worker.process)


def test_broker_scope_passes_cancellation_to_collection():
    from sico.transport.broker import skill_call_context
    with skill_call_context(cancelled=lambda: True):
        with pytest.raises(PdkUnavailable) as error:
            pdk_pool.check_cancelled()
        assert error.value.code == "collection_cancelled"
    pdk_pool.check_cancelled()
