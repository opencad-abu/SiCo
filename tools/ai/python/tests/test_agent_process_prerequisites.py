"""Reject unusable process ownership before spawning, with a visible diagnosis."""

import errno
import os
import signal
import subprocess

import pytest
import sico.local_process as local
from sico.local_process import LocalProcess
from sicoprocess import require_pidfds


def test_unavailable_subreaper_rejected_before_pipe_or_child(monkeypatch, tmp_path):
    def unavailable():
        raise ValueError("SiCo requires Linux child subreaper support")

    def forbidden(*args, **kwargs):
        pytest.fail("Prerequisite failure must not allocate pipes or spawn a child")

    monkeypatch.setattr(local, "require_subreaper", unavailable)
    monkeypatch.setattr(os, "pipe", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    with pytest.raises(ValueError, match="subreaper"):
        LocalProcess(["unused"], {}, tmp_path)


@pytest.mark.parametrize("operation", ["open", "signal"])
def test_kernel_rejection_is_actionable_and_closes_probe_fd(monkeypatch, operation):
    closed = []

    def unavailable(*args):
        raise OSError(errno.ENOSYS, "private host detail")

    monkeypatch.setattr(os, "pidfd_open",
                        unavailable if operation == "open" else lambda pid: 42, raising=False)
    monkeypatch.setattr(signal, "pidfd_send_signal", unavailable, raising=False)
    monkeypatch.setattr(os, "close", closed.append)
    with pytest.raises(ValueError, match="compatible host") as error:
        require_pidfds()
    assert "private host detail" not in str(error.value)
    assert closed == ([] if operation == "open" else [42])


def test_backend_reports_missing_ownership_without_submitting_or_replaying(monkeypatch, tmp_path):
    from sico.codex.backend import CodexBackend
    from sico.codex.settings import CodexSettings
    from sico.core.contracts import BoundContext
    from sico.core.tools import ToolRegistry
    from sico.storage.journal import SessionJournal

    def unavailable():
        raise ValueError("SiCo requires Linux child subreaper support")
    monkeypatch.setattr(local, "require_subreaper", unavailable)
    settings = CodexSettings({"provider": "responses", "base_url": "http://127.0.0.1:1/v1",
                              "model": "prerequisite-test"}, {"SICO_API_KEY": "test-secret"})
    monkeypatch.setattr(settings, "executable", lambda: "/unused/codex")
    context = BoundContext("instance", "generation", "target", {"valid": True})
    with SessionJournal(tmp_path, "missing-api") as journal:
        backend = CodexBackend(settings, ToolRegistry(), journal, context)
        try:
            events = list(backend.run("Do not send this request", input_id="probe"))
            assert events[-1]["kind"] == "task.needs_reconcile"
            assert "subreaper" in events[-1]["payload"]["message"]
            assert not any(e["kind"] in {"codex.thread", "tool.started"} for e in events)
        finally:
            backend.close()
