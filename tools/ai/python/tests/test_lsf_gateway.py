"""Real pipes and HTTP verify remote-only forwarding, redaction and cancellation."""

import os
import sys
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib import error, request

import pytest
from sico.codex.gateway_job import launcher
from sico.codex.lsf_gateway import LsfGateway
from sico.local_process import LocalProcess


@contextmanager
def upstream(status=200, streaming=False):
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            seen.append(
                (
                    self.path,
                    self.headers.get("Authorization"),
                    self.rfile.read(int(self.headers["Content-Length"])),
                )
            )
            body = (
                b'data: {"type":"response.completed"}\n\n'
                if streaming
                else b'{"ok":true}'
                if status == 200
                else b"sensitive-key-and-prompt"
            )
            self.send_response(status)
            self.send_header(
                "Content-Type", "text/event-stream" if streaming else "application/json"
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1/responses", seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


class PipeJob:
    instances = []

    def __init__(self, environment, cwd, _evidence):
        self.process = LocalProcess(
            [
                sys.executable,
                "-s",
                "-c",
                "from sico.codex.gateway_worker import main; raise SystemExit(main())",
            ],
            environment,
            cwd,
        )
        self.closed = False
        self.instances.append(self)

    def close(self):
        try:
            self.process.close()
        except (OSError, RuntimeError):
            pass
        try:
            self.process.child.stdout.close()
        except OSError:
            pass
        self.closed = True


class RecordJob(PipeJob):
    def __init__(self, environment, cwd, evidence):
        super().__init__(environment, cwd, evidence)
        self.record = {}

    def save(self):
        self.saved = dict(self.record)


def post(gateway, body=b'{"input":"hello"}', timeout=10):
    req = request.Request(
        gateway.base_url + "/responses",
        body,
        headers={"Authorization": "Bearer " + gateway.token},
    )
    try:
        response = request.urlopen(req, timeout=timeout)
    except error.HTTPError as exc:
        response = exc
    with response:
        return response.status, response.read()


@pytest.mark.parametrize(
    "status,streaming", [(200, False), (200, True), (401, False), (502, False)]
)
def test_http_forwarding_is_in_worker_and_errors_are_redacted(tmp_path, status, streaming):
    with upstream(status, streaming) as (endpoint, seen):
        settings = SimpleNamespace(
            environment=dict(os.environ),
            endpoint=endpoint,
            api_key="private-test-key",
            timeout=3,
            idle_timeout=3,
        )
        gateway = LsfGateway(settings, tmp_path, tmp_path, job_factory=PipeJob)
        try:
            req = request.Request(
                gateway.base_url + "/responses",
                b'{"input":"hello"}',
                headers={"Authorization": "Bearer " + gateway.token},
            )
            try:
                response = request.urlopen(req, timeout=10)
            except error.HTTPError as exc:
                response = exc
            with response:
                assert response.status == status
                body = response.read()
            assert b"sensitive-key" not in body and b"private-test-key" not in body
            assert seen == [("/v1/responses", "Bearer private-test-key", b'{"input":"hello"}')]
            if streaming:
                assert b"response.completed" in body
        finally:
            gateway.close()
        assert PipeJob.instances[-1].closed
        assert PipeJob.instances[-1].process.child.poll() is not None


def test_unauthorized_requests_never_submit_jobs(tmp_path):
    settings = SimpleNamespace(
        environment={"SICO_CODEX_LSF_REUSE": "0"},
        endpoint="https://unused/v1/responses",
        api_key="private",
        timeout=3,
        idle_timeout=3,
    )
    gateway = LsfGateway(
        settings,
        tmp_path,
        tmp_path,
        job_factory=lambda *_a: pytest.fail("Unauthorized job submission"),
    )
    try:
        with pytest.raises(error.HTTPError) as caught:
            request.urlopen(request.Request(gateway.base_url + "/responses", b"{}"), timeout=3)
        assert caught.value.code == 401
    finally:
        gateway.close()


def test_worker_runtime_error_reaches_the_local_diagnostic(tmp_path):
    class ErrorJob:
        def __init__(self, environment, cwd, _evidence):
            self.process = LocalProcess(
                [
                    sys.executable,
                    "-s",
                    "-c",
                    "import sys; from sico.codex.gateway_wire import encode, send; "
                    "send(sys.stdout.buffer, encode('error', message='TLS trust missing'))",
                ],
                environment,
                cwd,
            )
            self.closed = False

        def close(self):
            self.process.close()
            self.process.child.stdout.close()
            self.closed = True

    settings = SimpleNamespace(
        environment=dict(
            os.environ,
            SICO_CODEX_LSF_EXE="/bin/true",
            SICO_CODEX_LSF_ARGS="-Is -q test",
        ),
        endpoint="https://upstream.example/v1/responses",
        api_key="private-test-key",
        timeout=3,
        idle_timeout=3,
    )
    gateway = LsfGateway(settings, tmp_path, tmp_path, job_factory=ErrorJob)
    try:
        request_object = request.Request(
            gateway.base_url + "/responses",
            b'{"input":"hello"}',
            headers={"Authorization": "Bearer " + gateway.token},
        )
        with pytest.raises(error.HTTPError) as caught:
            request.urlopen(request_object, timeout=10)
        assert caught.value.code == 502
        assert b"TLS trust missing" in caught.value.read()
    finally:
        gateway.close()


def test_resident_worker_reuses_one_job_for_multiple_requests(tmp_path):
    PipeJob.instances.clear()
    with upstream(200, False) as (endpoint, seen):
        settings = SimpleNamespace(
            environment=dict(
                os.environ,
                SICO_CODEX_LSF_REUSE="1",
                SICO_CODEX_LSF_SESSION_IDLE="600",
            ),
            endpoint=endpoint,
            api_key="private-test-key",
            timeout=3,
            idle_timeout=3,
        )
        gateway = LsfGateway(settings, tmp_path, tmp_path, job_factory=PipeJob)
        try:
            assert post(gateway)[0] == 200
            assert post(gateway)[0] == 200
            assert len(PipeJob.instances) == 1
            assert len(seen) == 2
        finally:
            gateway.close()
        assert PipeJob.instances[0].closed


def test_resident_worker_records_request_and_session_counters(tmp_path):
    RecordJob.instances.clear()
    with upstream(200, False) as (endpoint, _seen):
        settings = SimpleNamespace(
            environment=dict(os.environ, SICO_CODEX_LSF_REUSE="1"),
            endpoint=endpoint,
            api_key="private-test-key",
            timeout=3,
            idle_timeout=3,
        )
        gateway = LsfGateway(settings, tmp_path, tmp_path, job_factory=RecordJob)
        try:
            assert post(gateway)[0] == 200
            assert post(gateway)[0] == 200
            job = RecordJob.instances[0]
            assert job.record["requests"] == 2
            assert job.record["session"]["jobs_submitted"] == 1
            assert job.record["session"]["requests_served"] == 2
        finally:
            gateway.close()


def test_resident_worker_releases_after_idle_and_resubmits(tmp_path):
    PipeJob.instances.clear()
    with upstream(200, False) as (endpoint, seen):
        settings = SimpleNamespace(
            environment=dict(
                os.environ,
                SICO_CODEX_LSF_REUSE="1",
                SICO_CODEX_LSF_SESSION_IDLE="1",
            ),
            endpoint=endpoint,
            api_key="private-test-key",
            timeout=3,
            idle_timeout=3,
        )
        gateway = LsfGateway(settings, tmp_path, tmp_path, job_factory=PipeJob)
        try:
            assert post(gateway)[0] == 200
            first = PipeJob.instances[-1]
            deadline = time.monotonic() + 8
            while not first.closed and time.monotonic() < deadline:
                time.sleep(0.1)
            assert first.closed
            assert post(gateway)[0] == 200
            assert len(PipeJob.instances) == 2
            assert len(seen) == 2
        finally:
            gateway.close()


def test_auxiliary_gateway_does_not_reuse(tmp_path):
    PipeJob.instances.clear()
    with upstream(200, False) as (endpoint, seen):
        settings = SimpleNamespace(
            environment=dict(os.environ, SICO_CODEX_LSF_REUSE="1"),
            endpoint=endpoint,
            api_key="private-test-key",
            timeout=3,
            idle_timeout=3,
        )
        gateway = LsfGateway(
            settings, tmp_path, tmp_path, job_factory=PipeJob, auxiliary=True
        )
        try:
            assert post(gateway)[0] == 200
            assert post(gateway)[0] == 200
            assert len(PipeJob.instances) == 2
            assert len(seen) == 2
        finally:
            gateway.close()


def test_dead_resident_worker_is_replaced_before_request(tmp_path):
    PipeJob.instances.clear()
    with upstream(200, False) as (endpoint, seen):
        settings = SimpleNamespace(
            environment=dict(os.environ, SICO_CODEX_LSF_REUSE="1"),
            endpoint=endpoint,
            api_key="private-test-key",
            timeout=3,
            idle_timeout=3,
        )
        gateway = LsfGateway(settings, tmp_path, tmp_path, job_factory=PipeJob)
        try:
            assert post(gateway)[0] == 200
            first = PipeJob.instances[-1]
            first.process.child.kill()
            first.process.child.wait(timeout=5)
            assert post(gateway)[0] == 200
            assert len(PipeJob.instances) == 2
            assert len(seen) == 2
        finally:
            gateway.close()


def test_resident_worker_recycles_before_site_kill(tmp_path):
    PipeJob.instances.clear()
    with upstream(200, False) as (endpoint, seen):
        settings = SimpleNamespace(
            environment=dict(
                os.environ,
                SICO_CODEX_LSF_REUSE="1",
                SICO_CODEX_LSF_SESSION_IDLE="2",
                SICO_CODEX_LSF_MAX_AGE="1",
            ),
            endpoint=endpoint,
            api_key="private-test-key",
            timeout=3,
            idle_timeout=3,
        )
        gateway = LsfGateway(settings, tmp_path, tmp_path, job_factory=PipeJob)
        try:
            assert post(gateway)[0] == 200
            time.sleep(2.5)
            assert post(gateway)[0] == 200
            assert len(PipeJob.instances) == 2
            assert len(seen) == 2
        finally:
            gateway.close()


def test_busy_resident_worker_uses_a_temporary_job(tmp_path):
    PipeJob.instances.clear()
    first_seen = threading.Event()
    release_first = threading.Event()
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            index = len(seen)
            seen.append(index)
            if index == 0:
                first_seen.set()
                release_first.wait(8)
            body = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    settings = SimpleNamespace(
        environment=dict(os.environ, SICO_CODEX_LSF_REUSE="1"),
        endpoint=f"http://127.0.0.1:{server.server_port}/v1/responses",
        api_key="private-test-key",
        timeout=5,
        idle_timeout=5,
    )
    gateway = LsfGateway(settings, tmp_path, tmp_path, job_factory=PipeJob)
    first = []
    try:
        worker = threading.Thread(target=lambda: first.append(post(gateway)))
        worker.start()
        assert first_seen.wait(5)
        assert post(gateway)[0] == 200
        assert len(PipeJob.instances) == 2
        release_first.set()
        worker.join(timeout=10)
        assert first and first[0][0] == 200
        assert len(seen) == 2
    finally:
        release_first.set()
        gateway.close()
        server.shutdown()
        server.server_close()
        thread.join(2)


@pytest.mark.parametrize("args", ["-Is -q ai -o leak", "-q ai", "-Is", "-Is -q ai -J overwrite"])
def test_launcher_rejects_redirection_or_missing_queue(args):
    with pytest.raises(ValueError):
        launcher(dict(SICO_CODEX_LSF_EXE="/bin/true", SICO_CODEX_LSF_ARGS=args))


def test_disconnect_cancels_upstream_and_reaps_worker(tmp_path):
    import socket
    import time

    accepted = threading.Event()
    disconnected = threading.Event()

    class Stall(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            accepted.set()
            self.connection.settimeout(8)
            if self.connection.recv(1) == b"":
                disconnected.set()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Stall)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    settings = SimpleNamespace(
        environment=dict(os.environ),
        endpoint=f"http://127.0.0.1:{server.server_port}/v1/responses",
        api_key="private",
        timeout=3,
        idle_timeout=30,
    )
    gateway = LsfGateway(settings, tmp_path, tmp_path, job_factory=PipeJob)
    try:
        from urllib.parse import urlsplit

        url = urlsplit(gateway.base_url)
        client = socket.create_connection((url.hostname, url.port), 3)
        client.sendall(
            (
                "POST /v1/responses HTTP/1.1\r\nHost: localhost\r\n"
                f"Authorization: Bearer {gateway.token}\r\n"
                "Content-Length: 2\r\n\r\n{}"
            ).encode()
        )
        assert accepted.wait(5)
        client.close()
        assert disconnected.wait(5)
        deadline = time.monotonic() + 5
        while not PipeJob.instances[-1].closed and time.monotonic() < deadline:
            time.sleep(0.05)
        assert PipeJob.instances[-1].closed
    finally:
        gateway.close()
        server.shutdown()
        server.server_close()
        thread.join(2)
