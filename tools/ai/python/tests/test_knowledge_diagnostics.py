"""Knowledge workers return stable categories while retaining private causes."""

import sys
import threading
import time

import pytest

from sico.diagnostics import DiagnosticLog
from sico.knowledge import jobs
from sico.knowledge.contracts import KnowledgeError
from sico.service.knowledge_checks import _diagnostic


def _python(tmp_path, body):
    path = tmp_path / "fake-python"
    path.write_text(f"#!{sys.executable}\n{body}\n")
    path.chmod(0o700)
    return path


def _run(tmp_path, monkeypatch, body, *, deadline=5, cancelled=lambda: False):
    tmp_path.mkdir(parents=True, exist_ok=True)
    tmp_path.chmod(0o700)
    python = _python(tmp_path, body)
    monkeypatch.setattr(jobs, "cad_python", lambda _environment: str(python))
    diagnostic = DiagnosticLog(
        tmp_path / "knowledge.jsonl", component="knowledge-test",
        secrets_=("secret-token",),
    )
    source = {"id": "fake", "type": "local", "enabled": True}
    try:
        jobs.run(
            tmp_path, source, "status", {}, {}, cancelled,
            time.monotonic() + deadline, diagnostic,
        )
    except KnowledgeError as exc:
        return exc, diagnostic
    raise AssertionError("fake worker should have failed")


@pytest.mark.parametrize("body,category,worker_code", [
    ('import json,sys; print(json.dumps({"ok": False, "code": "auth_required", '
     '"message": "需要凭据 secret-token"}))', "auth_failed", "auth_required"),
    ('import json,sys; print(json.dumps({"ok": False, "code": "unavailable", '
     '"message": "failed", "exception": "KeyError"}))',
     "operation_failed", "unavailable"),
])
def test_worker_rejection_has_stable_category_id_and_no_message_copy(
        tmp_path, monkeypatch, body, category, worker_code):
    error, diagnostic = _run(tmp_path, monkeypatch, body)
    assert error.code == worker_code and error.category == category
    assert error.diagnostic_id == diagnostic.correlation_id
    text = diagnostic.path.read_text()
    assert category in text and error.diagnostic_id in text
    assert "secret-token" not in text


def test_worker_protocol_and_import_failures_are_distinct(tmp_path, monkeypatch):
    protocol, protocol_log = _run(
        tmp_path / "protocol", monkeypatch,
        'import sys; print("not-json"); sys.exit(0)',
    )
    assert protocol.category == "protocol_frame_invalid"
    assert protocol.diagnostic_id == protocol_log.correlation_id
    assert "protocol_frame_invalid" in protocol_log.path.read_text()

    imported, import_log = _run(
        tmp_path / "import", monkeypatch,
        "import sys\n"
        "sys.stderr.write(\"ModuleNotFoundError: No module named 'private'\\n\")\n"
        "sys.exit(1)",
    )
    assert imported.category == "import_failed"
    assert imported.diagnostic_id == import_log.correlation_id
    assert "import_failed" in import_log.path.read_text()


def test_valid_output_with_nonzero_exit_is_not_accepted(tmp_path, monkeypatch):
    error, diagnostic = _run(
        tmp_path, monkeypatch,
        'import json,sys; print(json.dumps({"ok": True, "value": {"status": "ready"}})); '
        "sys.exit(3)",
    )
    assert error.code == "unavailable"
    assert error.category == "process_exit"
    assert error.diagnostic_id == diagnostic.correlation_id
    assert "process_exit" in diagnostic.path.read_text()


@pytest.mark.parametrize("mode", ["timeout", "cancelled"])
def test_worker_timeout_and_cancel_remain_distinct(tmp_path, monkeypatch, mode):
    stop = threading.Event()
    if mode == "cancelled":
        timer = threading.Timer(0.2, stop.set)
        timer.start()
    else:
        timer = None
    try:
        error, diagnostic = _run(
            tmp_path, monkeypatch,
            "import time; time.sleep(3); print('{}')",
            deadline=.3 if mode == "timeout" else 3,
            cancelled=stop.is_set,
        )
    finally:
        if timer is not None:
            timer.cancel()
    assert error.code == mode and error.category == mode
    assert error.diagnostic_id == diagnostic.correlation_id
    assert mode in diagnostic.path.read_text()


def test_service_uses_the_project_private_knowledge_log(tmp_path):
    tmp_path.chmod(0o700)
    for path in (tmp_path / ".sico", tmp_path / ".sico/ai",
                 tmp_path / ".sico/ai/agent"):
        path.mkdir(mode=0o700)
    secret = "service-secret-sentinel"
    diagnostic = _diagnostic(tmp_path, {"SICO_API_KEY": secret})
    assert diagnostic.enabled
    diagnostic.emit("knowledge.request.failed", category="unexpected_exception",
                    stderr=secret)
    assert diagnostic.path.parent == tmp_path / ".sico/ai/agent/knowledge"
    assert diagnostic.path.stat().st_mode & 0o077 == 0
    assert secret not in diagnostic.path.read_text()
