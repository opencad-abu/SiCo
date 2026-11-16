"""Configured model idle budgets survive the real worker wire boundary."""

import os
from types import SimpleNamespace

import pytest
from sico.codex.gateway_worker import _request
from sico.codex.lsf_gateway import LsfGateway
from sico.configuration.codex import codex_idle_timeout
from test_lsf_gateway import PipeJob, post, upstream


@pytest.mark.parametrize("idle", [3601, 86400])
def test_worker_forwards_full_configured_idle_range(tmp_path, idle):
    assert codex_idle_timeout({}, idle) == idle
    with upstream() as (endpoint, seen):
        settings = SimpleNamespace(environment=dict(os.environ), endpoint=endpoint,
                                   api_key="test-secret", timeout=3, idle_timeout=idle)
        gateway = LsfGateway(settings, tmp_path, tmp_path / "evidence", job_factory=PipeJob)
        try:
            status, body = post(gateway)
            assert status == 200 and body == b'{"ok":true}'
            assert len(seen) == 1
        finally:
            gateway.close()


@pytest.mark.parametrize("idle", [True, False, 0, -1, .5, 86401,
                                float("nan"), float("inf"), float("-inf")])
def test_worker_rejects_invalid_idle_budget(idle):
    with pytest.raises(ValueError):
        _request(dict(endpoint="http://127.0.0.1:1/v1/responses", key="test-secret",
                      timeout=3, idle=idle))
