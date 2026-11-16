"""Headless session bridge files and owned-process cleanup without Virtuoso."""

from __future__ import annotations

import json
import signal
from pathlib import Path
from types import SimpleNamespace

import pytest

from cadai import headless_session


class Process:
    def __init__(self, polls=()):
        self.polls = iter(polls)
        self.waits = []
        self.pid = 123

    def poll(self):
        return next(self.polls, 0)

    def wait(self, timeout=None):
        self.waits.append(timeout)
        return 0


def test_wait_file_returns_created_file_or_reports_exit(tmp_path):
    value = headless_session.NativeBridge(tmp_path, Process([None]))
    target = tmp_path / "ready"
    target.write_text("yes")
    value.wait_file(target, timeout=1)
    with pytest.raises(headless_session.HeadlessError, match="exited"):
        headless_session.NativeBridge(tmp_path, Process([1])).wait_file(
            tmp_path / "missing", timeout=1)
    with pytest.raises(TimeoutError, match="timeout"):
        headless_session.NativeBridge(tmp_path, Process([None])).wait_file(
            tmp_path / "missing", timeout=0)


def test_bridge_request_rejects_unknown_method_and_routes_known_methods():
    value = headless_session.NativeBridge(Path("/tmp"), Process())
    with pytest.raises(headless_session.HeadlessError, match="unsupported"):
        value.request({"method": "unknown"}, 1)
    calls = []
    value.evaluate_path = lambda *args, **kwargs: calls.append((args, kwargs))
    assert value.request({
        "method": "eval_skill", "source_path": "s", "output_path": "o"}, 2
    ) == (True, {"result_path": "o"})
    assert calls[-1][1]["evaluator"] == "aiEvalExpression"
    assert value.request({
        "method": "eval_skill_native", "source_path": "s", "output_path": "o"}, 2
    )[0] is True
    assert calls[-1][1]["evaluator"] == "aiEvalNativeExpression"


def test_evaluate_path_writes_atomic_command_and_waits(tmp_path, monkeypatch):
    value = headless_session.NativeBridge(tmp_path, Process())
    seen = []
    monkeypatch.setattr(headless_session.uuid, "uuid4",
                        lambda: SimpleNamespace(hex="tag"))
    value.wait_file = lambda path, timeout=50: seen.append((path, timeout))
    value.evaluate_path("source.il", "output.json", timeout=4)
    assert (tmp_path / "command.il").read_text().startswith("progn(")
    assert "source.il" in (tmp_path / "command.il").read_text()
    assert not (tmp_path / "pending.il").exists()
    assert seen[0][0].name == "tag.done"


def test_skill_reads_ok_result_and_reports_skill_failure(tmp_path):
    value = headless_session.NativeBridge(tmp_path, Process())
    def evaluate(source, output, **_kwargs):
        Path(output).write_text(json.dumps({
            "ok": source.read_text() == "good", "value": 7}))
    value.evaluate_path = evaluate
    assert value.skill("good") == 7
    with pytest.raises(headless_session.HeadlessError):
        value.skill("bad")


def test_dispatch_client_and_mcp_call_map_dispatcher_results():
    calls = []
    client = headless_session.DispatchClient(
        lambda identity, name, args: calls.append((identity, name, args)) or {"ok": 1})
    assert client.call("tool", {"x": 1}) == (True, {"ok": 1})
    assert len(calls[0][0]) == 32

    class Server:
        def __init__(self, response):
            self.response = response

        def handle(self, _request):
            return self.response

    assert headless_session.mcp_call(Server({
        "result": {"content": [{"text": '{"value": 2}'}]},
    }), "tool", {}) == {"value": 2}
    with pytest.raises(headless_session.HeadlessError):
        headless_session.mcp_call(Server({"error": {"code": "bad"}}), "tool", {})


def test_session_requires_xvfb_and_virtuoso(tmp_path, monkeypatch):
    monkeypatch.setattr(headless_session.shutil, "which",
                        lambda _name: None)
    with pytest.raises(headless_session.HeadlessUnavailable):
        with headless_session.session(tmp_path):
            pass
    monkeypatch.setattr(
        headless_session.shutil, "which",
        lambda name: "/bin/xvfb-run" if name == "xvfb-run" else None)
    with pytest.raises(headless_session.HeadlessUnavailable):
        with headless_session.session(tmp_path):
            pass


def test_session_spawns_owned_process_and_cleans_children(tmp_path, monkeypatch):
    process = Process([None, None])
    events = []
    monkeypatch.setattr(headless_session.shutil, "which",
                        lambda name: "/bin/xvfb-run" if name == "xvfb-run" else "/bin/virtuoso")
    monkeypatch.setattr(headless_session, "AI_ROOT", tmp_path / "ai")
    monkeypatch.setattr(headless_session, "IC_LIBS", tmp_path / "libs")
    monkeypatch.setattr(headless_session.subprocess, "Popen",
                        lambda *args, **kwargs: events.append((args, kwargs)) or process)
    monkeypatch.setattr(headless_session.os, "killpg",
                        lambda pid, number: events.append(("kill", pid, number)))
    class Bridge:
        def __init__(self, directory, spawned):
            self.directory, self.process = directory, spawned
            directory.joinpath("ready").write_text("yes")
        def wait_file(self, *_args, **_kwargs):
            pass
    monkeypatch.setattr(headless_session, "NativeBridge", Bridge)
    monkeypatch.setattr(headless_session, "RequestDispatcher",
                        lambda *args, **kwargs: "dispatcher")
    monkeypatch.setattr(headless_session, "RuntimePaths",
                        lambda *args, **kwargs: "paths")
    monkeypatch.setattr(headless_session, "DispatchClient",
                        lambda dispatcher: ("client", dispatcher))
    monkeypatch.setattr(headless_session, "McpServer",
                        lambda client, workspace: ("server", client, workspace))
    path = tmp_path / "session"
    with headless_session.session(path) as (bridge, server, spawned):
        assert bridge.process is spawned is process
        assert server[0] == "server" and (path / "ready").exists()
        assert (path / "run.il").exists() and (path / "spool").is_dir()
    assert ("kill", process.pid, signal.SIGTERM) in events
    assert process.waits == [5]
