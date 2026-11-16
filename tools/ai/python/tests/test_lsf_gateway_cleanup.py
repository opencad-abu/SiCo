"""Scheduler uncertainty stays explicit even after the local launcher is gone."""

from types import SimpleNamespace

import pytest
from sico.codex.gateway_job import GatewayJob


def test_unknown_submission_is_retained_for_reconciliation(tmp_path):
    job = object.__new__(GatewayJob)
    job.process = None
    job.path = tmp_path / "cleanup.json"
    job.expected = dict(name="sico-ai-test", owner="test", cluster="test")
    job.record = dict(phase="submission_intent", scheduler=job.expected)
    job.scheduler = SimpleNamespace(observe=lambda _expected: (_ for _ in ()).throw(OSError()))
    with pytest.raises(RuntimeError, match="cleanup unconfirmed"):
        job.close()
    assert job.record["phase"] == "cleanup_unconfirmed"
    assert "cleanup_unconfirmed" in job.path.read_text()
    assert job.path.stat().st_mode & 0o777 == 0o600


def test_scheduler_cleanup_runs_even_if_local_reaping_failed(tmp_path):
    actions = []
    job = object.__new__(GatewayJob)
    job.process = SimpleNamespace(close=lambda: (_ for _ in ()).throw(RuntimeError()))
    job.path = tmp_path / "cleanup.json"
    job.expected = dict(name="sico-ai-test", owner="test", cluster="test")
    job.record = dict(phase="submission_intent", scheduler=job.expected)

    def observe(_expected):
        actions.append("observe")
        return dict(state="DONE", job_id="12")

    job.scheduler = SimpleNamespace(observe=observe)
    with pytest.raises(RuntimeError, match="cleanup unconfirmed"):
        job.close()
    assert actions == ["observe"]
    assert job.record["phase"] == "cleanup_unconfirmed"
