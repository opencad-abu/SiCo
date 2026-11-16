"""LSF gateway rejects malformed local requests before scheduling work."""

from __future__ import annotations

import os
import socket
from types import SimpleNamespace
from urllib import error, request

import pytest
from sico.codex.gateway_session import WorkerError
from sico.codex.lsf_gateway import LsfGateway
from test_lsf_gateway import PipeJob, post, upstream


def settings():
    return SimpleNamespace(
        environment={"SICO_CODEX_LSF_REUSE": "0"},
        endpoint="https://unused/v1/responses",
        api_key="private",
        timeout=3,
        idle_timeout=3,
    )


def raw_post(gateway, headers):
    connection = socket.create_connection(
        ("127.0.0.1", gateway.server.server_address[1]), timeout=3)
    try:
        lines = ["POST /v1/responses HTTP/1.1", "Host: localhost"]
        lines.extend(headers)
        lines.append("Connection: close")
        connection.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        return connection.recv(4096).split(b"\r\n", 1)[0]
    finally:
        connection.close()


def test_gateway_rejects_unknown_path_without_job(tmp_path):
    gateway = LsfGateway(settings(), tmp_path, tmp_path,
                         job_factory=lambda *_args: pytest.fail("job submitted"))
    try:
        with pytest.raises(error.HTTPError) as caught:
            request.urlopen(request.Request(
                gateway.base_url + "/other", b"{}",
                headers={"Authorization": "Bearer " + gateway.token}), timeout=3)
        assert caught.value.code == 404
    finally:
        gateway.close()


@pytest.mark.parametrize("headers", [
    ["Authorization: Bearer {token}", "Content-Length: 0"],
    ["Authorization: Bearer {token}", "Content-Length: 1",
     "Transfer-Encoding: chunked"],
    ["Authorization: Bearer {token}", "Content-Length: 1",
     "Content-Encoding: gzip"],
    ["Authorization: Bearer {token}", "Content-Length: 1",
     "Content-Length: 1"],
])
def test_gateway_rejects_bad_request_framing_before_job(tmp_path, headers):
    gateway = LsfGateway(settings(), tmp_path, tmp_path,
                         job_factory=lambda *_args: pytest.fail("job submitted"))
    try:
        headers = [value.format(token=gateway.token) for value in headers]
        assert b"400" in raw_post(gateway, headers)
    finally:
        gateway.close()


def test_resident_worker_error_falls_back_to_temporary_and_reports(
        tmp_path, monkeypatch):
    gateway = LsfGateway(settings(), tmp_path, tmp_path,
                         job_factory=lambda *_args: pytest.fail("job submitted"))
    class Worker:
        stats = {"worker_failures": 0}

        def acquire(self):
            raise WorkerError("resident failed")

        def note_request(self):
            pass

        def close(self):
            pass

    gateway.worker = Worker()
    temporary = SimpleNamespace(
        acquire=lambda: (_ for _ in ()).throw(WorkerError("temporary failed")),
        close=lambda: None,
    )
    gateway.temporary_factory = lambda: temporary
    try:
        with pytest.raises(error.HTTPError) as caught:
            request.urlopen(request.Request(
                gateway.base_url + "/responses", b"{}",
                headers={"Authorization": "Bearer " + gateway.token}), timeout=3)
        assert caught.value.code == 502
        assert gateway.diagnostic == "temporary failed"
        assert gateway.worker.stats["worker_failures"] == 1
    finally:
        gateway.close()


def test_cleanup_failure_is_visible_and_close_raises(tmp_path):
    with upstream(200, False) as (endpoint, _seen):
        values = settings()
        values.environment = dict(
            os.environ, SICO_CODEX_LSF_REUSE="1", SICO_CODEX_LSF_SESSION_IDLE="600")
        values.endpoint = endpoint
        gateway = LsfGateway(values, tmp_path, tmp_path, job_factory=PipeJob)
        gateway.worker.release = lambda **_kwargs: (_ for _ in ()).throw(
            RuntimeError("cleanup failed"))
        try:
            assert post(gateway)[0] == 200
            assert gateway.cleanup_failed is True
            with pytest.raises(RuntimeError, match="reconciliation"):
                gateway.close()
        finally:
            if not gateway.stopped.is_set():
                gateway.close()


def test_cleanup_error_flag_is_not_hidden_on_close(tmp_path):
    gateway = LsfGateway(settings(), tmp_path, tmp_path)
    gateway.cleanup_failed = True
    with pytest.raises(RuntimeError, match="reconciliation"):
        gateway.close()
