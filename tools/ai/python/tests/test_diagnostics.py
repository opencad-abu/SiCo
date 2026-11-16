"""Private diagnostic evidence is bounded, redacted and category-stable."""

import json

from sico import diagnostics


def _files(log):
    return [log.path, *[log.path.with_name(log.path.name + "." + str(i))
                        for i in range(1, log.backups + 1)]]


def _records(log):
    rows = []
    for path in _files(log):
        if path.exists():
            rows.extend(json.loads(line) for line in path.read_text().splitlines())
    return rows


def test_private_log_has_utf8_byte_limits_and_fixed_retention(tmp_path):
    tmp_path.chmod(0o700)
    log = diagnostics.DiagnosticLog(
        tmp_path / "diagnostics.jsonl", component="test",
        max_bytes=1024, backups=2,
    )
    for index in range(24):
        assert log.emit("test.failure", category="process_exit", exception="OSError",
                        stage="stage", exit_code=index, stderr="错" * 900)

    files = [path for path in _files(log) if path.exists()]
    assert len(files) == 3
    assert not log.path.with_name(log.path.name + ".3").exists()
    assert all(path.stat().st_size <= log.max_bytes for path in files)
    rows = _records(log)
    assert rows and all(row["schema"] == diagnostics.SCHEMA for row in rows)
    assert all(row["correlation_id"] == log.correlation_id for row in rows)
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in files)
    assert all(len(row.get("stderr", "").encode()) <= 8192 for row in rows)


def test_redaction_covers_values_headers_urls_and_unknown_fields(tmp_path):
    tmp_path.chmod(0o700)
    log = diagnostics.DiagnosticLog(
        tmp_path / "diagnostics.jsonl", component="test",
        secrets_=("exact-sentinel",),
    )
    log.emit(
        "test.failure",
        stderr=(
            "exact-sentinel Authorization: Bearer abcdefghijklmnop "
            "api_key=another-sentinel https://user:password@example.invalid/path"
        ),
        command="exact-sentinel --token another-sentinel",
        stage="spawn",
    )
    text = log.path.read_text()
    assert "exact-sentinel" not in text
    assert "another-sentinel" not in text
    assert "abcdefghijklmnop" not in text
    assert "password@example" not in text
    assert "command" not in text
    assert "[REDACTED]" in text


def test_invalid_or_nonprivate_location_does_not_write_or_raise(tmp_path):
    log = diagnostics.DiagnosticLog("relative.jsonl", component="test")
    assert not log.enabled and log.emit("test.failure", category="x") is None
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o755)
    shared.chmod(0o755)
    disabled = diagnostics.DiagnosticLog(shared / "diagnostics.jsonl", component="test")
    assert not disabled.enabled
    assert not (shared / "diagnostics.jsonl").exists()


def test_failure_categories_are_distinct_and_stable():
    assert diagnostics.classify_text("ModuleNotFoundError: No module named 'x'") == (
        diagnostics.IMPORT_FAILED)
    assert diagnostics.classify_text("HTTP 401 Unauthorized") == diagnostics.AUTH_FAILED
    assert diagnostics.classify_text("worker exited") == diagnostics.PROCESS_EXIT
    assert diagnostics.classify_rpc_error(401, "private body") == diagnostics.AUTH_FAILED
    assert diagnostics.classify_rpc_error(-32700, "private body") == (
        diagnostics.UPSTREAM_PROTOCOL_ERROR)
    assert diagnostics.classify_rpc_error(-32000, "private body") == (
        diagnostics.UPSTREAM_REJECTED)
    assert {diagnostics.TIMEOUT, diagnostics.CANCELLED,
            diagnostics.CLEANUP_UNCONFIRMED} == {
                "timeout", "cancelled", "cleanup_unconfirmed"}
