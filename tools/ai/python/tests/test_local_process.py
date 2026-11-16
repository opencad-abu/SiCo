"""Shared local ownership keeps failure cleanup and stdio custody explicit."""

import os
import select
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from sico.diagnostics import DiagnosticLog, MAX_STDERR_BYTES
from sico.local_process import LocalProcess


def test_legacy_import_is_the_same_process_owner():
    from sico.codex.local_process import LocalProcess as LegacyProcess

    assert LegacyProcess is LocalProcess


def test_invalid_installation_does_not_allocate_control_pipes(monkeypatch, tmp_path):
    monkeypatch.setenv("SICO_HOME", str(tmp_path / "missing"))
    monkeypatch.delenv("CAD_HOME", raising=False)

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid installation must be rejected before pipe allocation or spawn")

    monkeypatch.setattr(os, "pipe", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    with pytest.raises(ValueError, match="installation identity"):
        LocalProcess(["unused"], {}, tmp_path)


def test_spawn_failure_closes_both_control_ends_without_mutating_environment(monkeypatch, tmp_path):
    allocated = []
    pipe = os.pipe
    environment = {"PYTHONPATH": "caller-owned", "SETTING": "kept"}

    def record_pipe():
        ends = pipe()
        allocated.extend(ends)
        return ends

    def unavailable(*args, **kwargs):
        assert kwargs["env"]["PYTHONPATH"] != "caller-owned"
        assert kwargs["env"]["SETTING"] == "kept"
        raise OSError("cannot launch supervisor")

    monkeypatch.setattr(os, "pipe", record_pipe)
    monkeypatch.setattr(subprocess, "Popen", unavailable)
    try:
        with pytest.raises(OSError, match="cannot launch supervisor"):
            LocalProcess(["unused"], environment, tmp_path)
        assert len(allocated) == 4
        for descriptor in allocated:
            with pytest.raises(OSError):
                os.fstat(descriptor)
        assert environment == {"PYTHONPATH": "caller-owned", "SETTING": "kept"}
    finally:
        for descriptor in allocated:
            try:
                os.close(descriptor)
            except OSError:
                pass


@pytest.mark.parametrize("failure", ["timeout", "nonzero", "already_exited", "success"])
def test_close_preserves_cleanup_classification_and_consumer_stdout(failure):
    events = []
    read_fd, write_fd = os.pipe()
    owner = object.__new__(LocalProcess)
    owner._lock = threading.Lock()
    owner._control = write_fd
    owner.diagnostic_id = "process-test"

    def wait(timeout):
        assert timeout == 2
        assert select.select([read_fd], [], [], .1)[0]
        assert os.read(read_fd, 1) == b""
        assert events == ["stdin_closed"]
        if failure == "timeout":
            raise subprocess.TimeoutExpired("supervisor", timeout)

    owner.child = SimpleNamespace(
        poll=lambda: 137 if failure == "already_exited" else None,
        stdin=SimpleNamespace(close=lambda: events.append("stdin_closed")),
        stdout=SimpleNamespace(close=lambda: pytest.fail("stdout belongs to its reader")),
        wait=wait,
        returncode=0 if failure == "success" else 137,
    )
    try:
        if failure in {"timeout", "nonzero"}:
            message = "still require reconciliation" if failure == "timeout" else "not confirmed"
            with pytest.raises(RuntimeError, match=message) as caught:
                owner.close()
            assert caught.value.code == "cleanup_unconfirmed"
            assert caught.value.diagnostic_id == "process-test"
        else:
            owner.close()
        assert owner._control == -1
    finally:
        os.close(read_fd)
        if owner._control >= 0:
            os.close(owner._control)


@pytest.mark.parametrize(
    "body,category",
    [
        ("import definitely_missing_private_module", "import_failed"),
        ("import sys; sys.stderr.write('401 Unauthorized api_key=s3cret-token'); sys.exit(3)",
         "auth_failed"),
        ("import sys; sys.stderr.write('plain worker failure'); sys.exit(4)", "process_exit"),
    ],
)
def test_child_stderr_is_private_and_exit_category_is_stable(tmp_path, body, category):
    tmp_path.chmod(0o700)
    log = DiagnosticLog(
        tmp_path / "diagnostics.jsonl", component="process-test",
        secrets_=("s3cret-token",),
    )
    owner = LocalProcess(
        [sys.executable, "-s", "-c", body], {}, tmp_path, diagnostic=log,
        component="process-test",
    )
    try:
        assert owner.wait(5) != 0
        owner.close()
        observed = owner.failure(.2)
        assert observed["code"] == category
        assert observed["diagnostic_id"] == log.correlation_id
        text = log.path.read_text()
        assert category in text
        assert "s3cret-token" not in text
        assert body not in text
    finally:
        owner.close()


def test_stderr_capture_keeps_only_the_bounded_tail(tmp_path):
    tmp_path.chmod(0o700)
    body = "import sys; sys.stderr.write('x' * 20000); sys.exit(2)"
    owner = LocalProcess([sys.executable, "-s", "-c", body], {}, tmp_path)
    try:
        assert owner.wait(5) == 2
        owner.close()
        assert len(owner.stderr_text().encode("utf-8")) <= MAX_STDERR_BYTES
        assert owner.stderr_text().endswith("x" * 128)
    finally:
        owner.close()
