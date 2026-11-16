"""Custodian evidence and shutdown decisions without forking a real worker tree."""

from __future__ import annotations

import os
import signal
from types import SimpleNamespace

import pytest
from sico.service import background_custody

IDENTITY = {
    "pid": 41,
    "start": "100",
    "job_id": "job",
    "worker_id": "worker",
    "instance_id": "instance",
    "generation": "generation",
}
WATCHER = dict(IDENTITY, pid=42, start="101")


class Clock:
    def __init__(self, values):
        self.values = iter(values)
        self.sleeps = []
        self.last = 0

    def monotonic(self):
        try:
            self.last = next(self.values)
        except StopIteration:
            pass
        return self.last

    def sleep(self, delay):
        self.sleeps.append(delay)

    def time(self):
        return 123.5


def config(**overrides):
    value = {
        "job_id": "job",
        "worker_id": "worker",
        "instance_id": "instance",
        "generation": "generation",
        "supervisor": {"pid": 7, "start": "70"},
        "shutdown_timeout": 0.01,
    }
    value.update(overrides)
    return value


def state(code, status):
    return SimpleNamespace(si_code=code, si_status=status)


def exited(status=0):
    return state(os.CLD_EXITED, status)


def install(monkeypatch, tmp_path, states, *, clock=None, alive=True):
    root = tmp_path / "job"
    root.mkdir(exist_ok=True)
    path = root / "connection.json"
    path.touch()
    reports, kills, handlers = [], [], {}
    values = iter(states)

    def waitid(*_args):
        row = next(values)
        if isinstance(row, BaseException):
            raise row
        if row == "signal":
            handlers[signal.SIGHUP]()
            return None
        return row

    monkeypatch.setattr(background_custody, "_identity", lambda _pid: dict(WATCHER))
    monkeypatch.setattr(background_custody, "_publish",
                        lambda _root, report: reports.append(dict(report)))
    monkeypatch.setattr(background_custody.os, "waitid", waitid)
    monkeypatch.setattr(background_custody.os, "kill",
                        lambda pid, number: kills.append((pid, number)))
    monkeypatch.setattr(background_custody.signal, "signal",
                        lambda number, callback: handlers.setdefault(number, callback))
    monkeypatch.setattr("sico.service.background_watchdog.process_alive",
                        lambda _identity: alive() if callable(alive) else alive)
    monkeypatch.setattr(background_custody.time, "monotonic",
                        clock.monotonic if clock else lambda: 0)
    monkeypatch.setattr(background_custody.time, "sleep",
                        clock.sleep if clock else lambda _delay: None)
    monkeypatch.setattr(background_custody, "reap_descendants_bounded",
                        lambda **_kwargs: {"stopped": True, "remaining": 0, "reaped": [9]})
    monkeypatch.setattr(background_custody, "watchdog_record",
                        lambda *_args: {"process_stopped": True})
    monkeypatch.setattr(
        background_custody,
        "owned_record",
        lambda _root, name, _identity: (
            {"launcher": "launcher"} if name == "watcher.json" else None
        ),
    )
    return path, reports, kills, handlers


def test_identity_and_report_cover_success_and_damage(monkeypatch):
    monkeypatch.setattr("sico.service.background_watchdog.process_identity",
                        lambda pid: {"pid": pid, "start": "1"})
    assert background_custody._identity(3) == {"pid": 3, "start": "1"}
    for error in (OSError("gone"), ValueError("bad"), IndexError("short")):
        monkeypatch.setattr(
            "sico.service.background_watchdog.process_identity",
            lambda _pid, error=error: (_ for _ in ()).throw(error),
        )
        assert background_custody._identity(3) is None

    monkeypatch.setattr("sico.service.background_watchdog.boot_id", lambda: "boot")
    report = background_custody._report(
        None, config(), {"pid": 40, "start": "90"}, dict(WATCHER)
    )
    assert report["protocol"] == "cad_ai_background_custody.v1"
    assert report["boot_id"] == "boot"
    assert report["phase"] == "armed"
    assert report["tree_owned"] is True
    assert report["process_stopped"] is False
    assert report["children_remaining"] is None
    assert report["supervisor"] == {"pid": 7, "start": "70"}
    assert report["watcher"] == WATCHER


def test_publish_writes_private_custody_record(tmp_path):
    background_custody._publish(tmp_path, {"phase": "armed", "value": 1})
    assert (tmp_path / "custody.json").read_text() == (
        '{"phase": "armed", "value": 1}\n'
    )


def test_own_records_normal_watcher_exit_and_registers_signals(tmp_path, monkeypatch):
    path, reports, kills, handlers = install(
        monkeypatch, tmp_path, [None, exited(7), exited(7)], clock=Clock([0])
    )
    status = background_custody.own(
        path, config(), {"pid": 40, "start": "90"}, WATCHER["pid"]
    )
    assert status == 7
    assert set(handlers) == {signal.SIGTERM, signal.SIGINT, signal.SIGHUP}
    assert kills == []
    assert reports[-1]["phase"] == "finished"
    assert reports[-1]["reason"] == "watcher_exited"
    assert reports[-1]["process_stopped"] is True
    assert reports[-1]["children_remaining"] == 0
    assert reports[-1]["reaped"] == [9]
    assert reports[-1]["launcher"] == "launcher"
    assert reports[-1]["exit_code"] == 7
    assert reports[-1]["automatic_resume_allowed"] is False


@pytest.mark.parametrize("reason", ["supervisor_lost", "stop_requested", "custodian_signal"])
def test_own_explains_why_shutdown_was_requested(tmp_path, monkeypatch, reason):
    alive = reason != "supervisor_lost"
    states = [None, exited(0), exited(0)]
    if reason == "custodian_signal":
        states = ["signal", exited(0), exited(0)]
    path, reports, kills, _handlers = install(
        monkeypatch, tmp_path, states, clock=Clock([0]), alive=alive
    )
    if reason == "stop_requested":
        (path.parent / "stop").touch()
    assert background_custody.own(path, config(), {"pid": 40, "start": "90"}, 42) == 0
    assert reports[-1]["reason"] == reason
    assert kills == [(42, signal.SIGCONT)]


def test_own_escalates_to_sigkill_after_the_shutdown_grace(tmp_path, monkeypatch):
    path, reports, kills, _handlers = install(
        monkeypatch,
        tmp_path,
        [None, None, state(os.CLD_KILLED, signal.SIGKILL), state(os.CLD_KILLED, signal.SIGKILL)],
        clock=Clock([0, 1]),
        alive=False,
    )
    assert background_custody.own(path, config(), {"pid": 40, "start": "90"}, 42) == 137
    assert kills == [(42, signal.SIGCONT), (42, signal.SIGCONT), (42, signal.SIGKILL)]
    assert reports[-1]["reason"] == "supervisor_lost"
    assert reports[-1]["exit_code"] == 137


def test_own_ignores_a_watcher_that_disappears_before_signal(tmp_path, monkeypatch):
    path, reports, _kills, _handlers = install(
        monkeypatch,
        tmp_path,
        [None, None, exited(0), exited(0)],
        clock=Clock([0, 1]),
        alive=False,
    )
    calls = []

    def gone(pid, number):
        calls.append((pid, number))
        raise ProcessLookupError

    monkeypatch.setattr(background_custody.os, "kill", gone)
    assert background_custody.own(path, config(), {"pid": 40, "start": "90"}, 42) == 0
    assert calls == [(42, signal.SIGCONT), (42, signal.SIGCONT),
                     (42, signal.SIGKILL)]
    assert reports[-1]["reason"] == "supervisor_lost"


@pytest.mark.parametrize("wake", ["watchdog", "stop", "supervisor", "deadline"])
def test_own_waits_for_the_execution_deadline_until_a_stop_condition(
        tmp_path, monkeypatch, wake):
    values = [None, exited(0), exited(0)]
    clock = Clock([0, 0, 11] if wake == "deadline" else [0, 0])
    path, reports, _kills, _handlers = install(
        monkeypatch, tmp_path, values, clock=clock
    )
    path.parent.mkdir(exist_ok=True)
    calls = {"watchdog": 0}

    def watchdog(*_args):
        calls["watchdog"] += 1
        if wake == "stop" and calls["watchdog"] == 2:
            (path.parent / "stop").touch()
        return ({"process_stopped": True}
                if wake == "watchdog" and calls["watchdog"] == 2 else None)

    alive_values = iter([True, False]) if wake == "supervisor" else iter([True] * 20)
    monkeypatch.setattr(background_custody, "watchdog_record", watchdog)
    monkeypatch.setattr(
        background_custody,
        "owned_record",
        lambda _root, name, _identity: (
            {"launcher": "launcher"} if name == "watcher.json" else
            {"deadline_monotonic": 10} if name == "execution.json" else None
        ),
    )
    monkeypatch.setattr("sico.service.background_watchdog.process_alive",
                        lambda _identity: next(alive_values, False))
    assert background_custody.own(path, config(), {"pid": 40, "start": "90"}, 42) == 0
    assert reports[-1]["reason"] == "watcher_exited"


@pytest.mark.parametrize("kill_error", [None, ProcessLookupError(), OSError("gone")])
def test_own_reports_custodian_failure_after_bounded_cleanup(
        tmp_path, monkeypatch, kill_error, capsys):
    _path, reports, _kills, _handlers = install(
        monkeypatch, tmp_path, [RuntimeError("wait failed")]
    )

    def kill(*_args):
        if kill_error is not None:
            raise kill_error

    monkeypatch.setattr(background_custody.os, "kill", kill)
    assert background_custody.own(
        tmp_path / "job/connection.json", config(), {"pid": 40, "start": "90"}, 42
    ) == 1
    assert reports[-1]["reason"] == "custodian_error"
    assert reports[-1]["process_stopped"] is True
    assert "wait failed" in capsys.readouterr().err


def test_run_reads_config_forks_and_reports_the_child_status(tmp_path, monkeypatch):
    path = tmp_path / "connection.json"
    seen = {}
    monkeypatch.setattr("sico.service.background_worker._read",
                        lambda _path: b'{"value": 1}')
    monkeypatch.setattr(background_custody, "enable_subreaper",
                        lambda: seen.setdefault("subreaper", True))
    monkeypatch.setattr("sico.service.background_watchdog.process_identity",
                        lambda pid: {"pid": pid, "start": "1"})
    monkeypatch.setattr(background_custody.os, "fork", lambda: 42)

    def own(received_path, received, custodian, watcher):
        seen.update(path=received_path, config=received, custodian=custodian,
                    watcher=watcher)
        return 5

    monkeypatch.setattr(background_custody, "own", own)
    assert background_custody.run(lambda *_args, **_kwargs: 0, path) == 5
    assert seen["path"] == path
    assert seen["config"] == {"value": 1}
    assert seen["custodian"] == {"pid": os.getpid(), "start": "1"}
    assert seen["watcher"] == 42


@pytest.mark.parametrize("failure", [False, True])
def test_run_child_executes_the_function_and_exits_with_its_status(
        tmp_path, monkeypatch, failure):
    path = tmp_path / "connection.json"
    exits = []
    seen = {}
    monkeypatch.setattr("sico.service.background_worker._read", lambda _path: b"{}")
    monkeypatch.setattr(background_custody, "enable_subreaper", lambda: None)
    monkeypatch.setattr("sico.service.background_watchdog.process_identity",
                        lambda _pid: {"pid": 1, "start": "1"})
    monkeypatch.setattr(background_custody.os, "fork", lambda: 0)
    monkeypatch.setattr(background_custody.os, "_exit", exits.append)

    def child(connection, *, custodian):
        seen.update(connection=connection, custodian=custodian)
        if failure:
            raise RuntimeError("child failed")
        return 9

    monkeypatch.setattr(background_custody, "own", lambda *_args: None)
    assert background_custody.run(child, path) is None
    assert seen["connection"] == path
    assert seen["custodian"] == {"pid": 1, "start": "1"}
    assert exits == [1 if failure else 9]
