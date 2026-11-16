"""Shared job control retains identity checks and bounded local CLI ownership."""

import os
import subprocess
import sys

import pytest
from agent_lsf_fixtures import config

from cadai.json_values import ProtocolError
from sico.lsf import commands, output
from sico.lsf.job_control import LsfJobControl
from sico.service import lsf_commands, lsf_output
from sico.service.lsf_scheduler import LsfScheduler


def test_legacy_aliases_and_background_adapter_have_one_shared_owner(tmp_path):
    for name in ("command_environment", "run_command"):
        assert getattr(lsf_commands, name) is getattr(commands, name)
    for name in ("STATES", "FIELDS", "missing", "submitted", "jobs", "history"):
        assert getattr(lsf_output, name) is getattr(output, name)
    assert LsfScheduler.__init__ is LsfJobControl.__init__
    for name in ("verify_cluster", "probe", "observe", "cancel"):
        assert getattr(LsfScheduler, name) is getattr(LsfJobControl, name)
    assert not hasattr(LsfJobControl, "submit")
    shared, adapter = LsfJobControl(config(tmp_path)), LsfScheduler(config(tmp_path))
    assert set(vars(shared)) == set(vars(adapter)) == {"config", "runner", "environment", "_mode"}


@pytest.mark.parametrize("failure", ["installation", "spawn"])
def test_setup_failure_releases_every_allocated_control_descriptor(monkeypatch, failure):
    allocated, environment = [], {"LANG": "C", "PYTHONPATH": "caller-owned"}
    pipe = os.pipe

    def record_pipe():
        ends = pipe()
        allocated.extend(ends)
        return ends

    def reject_path():
        raise ValueError("invalid installation")

    def reject_spawn(*args, **kwargs):
        assert kwargs["env"]["PYTHONPATH"] != "caller-owned"
        raise OSError("cannot launch supervisor")

    monkeypatch.setattr(os, "pipe", record_pipe)
    monkeypatch.setattr(commands.subprocess, "Popen", reject_spawn)
    if failure == "installation":
        monkeypatch.setattr(commands, "python_path", reject_path)
    try:
        with pytest.raises((ValueError, OSError), match="installation|supervisor"):
            commands.run_command(["unused"], environment=environment, timeout=1)
        assert len(allocated) == (0 if failure == "installation" else 2)
        for fd in allocated:
            with pytest.raises(OSError):
                os.fstat(fd)
        assert environment == {"LANG": "C", "PYTHONPATH": "caller-owned"}
    finally:
        for fd in allocated:
            try:
                os.close(fd)
            except OSError:
                pass


def test_decode_failure_reaps_supervisor_and_closes_output(monkeypatch):
    children = []
    spawn = subprocess.Popen

    def record_spawn(*args, **kwargs):
        child = spawn(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(commands.subprocess, "Popen", record_spawn)
    with pytest.raises(UnicodeDecodeError):
        commands.run_command([sys.executable, "-c", "import os;os.write(1,b'\\xff')"],
                             environment=commands.command_environment(), timeout=3)
    assert len(children) == 1 and children[0].poll() == 0 and children[0].stdout.closed


@pytest.mark.parametrize("fault", ["owner", "cluster", "ambiguous", "recycled"])
def test_unverified_identity_never_dispatches_cancellation(tmp_path, fault):
    calls = []
    c = config(tmp_path)
    expected = dict(cluster=c.cluster, owner=c.owner, name="original", job_id="42")
    if fault in {"owner", "cluster"}:
        expected[fault] = "other"

    def runner(argv, **kwargs):
        calls.append(argv)
        if argv[0] == c.lsid:
            return 0, "My cluster name is eda-test\n"
        row = "43" if fault == "recycled" else "42"
        raw = f"{row}|{c.owner}|RUN|original\n"
        return 0, raw * (2 if fault == "ambiguous" else 1)

    control = LsfJobControl(c, runner=runner)
    control._mode = "fields"
    with pytest.raises(ProtocolError):
        control.cancel(expected)
    assert not any(argv[0] == c.bkill for argv in calls)


@pytest.mark.parametrize("state,ack", [("DONE", "terminal_observed"), ("EXIT", "terminal_observed"),
                                       ("UNKWN", "unconfirmed"), ("RUN", "requested")])
def test_cancel_acknowledges_request_separately_from_terminal_observation(tmp_path, state, ack):
    calls = []
    expected = dict(cluster="eda-test", owner="owner", name="original", job_id="42", state=state)
    control = LsfJobControl(config(tmp_path), runner=lambda argv, **kw: (calls.append(argv) or 0, ""))
    control.observe = lambda value: dict(value)
    assert control.cancel(expected) == ack
    assert expected["state"] == state
    assert calls == ([[control.config.bkill, "-J", "original", "0"]] if state == "RUN" else [])
