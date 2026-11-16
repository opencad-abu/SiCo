"""Service preview index/read: manifest, manual file, tamper and wire round trip."""

import threading

import pytest
from sico.service.frontend_assets import (
    preview_content_record,
    preview_content_value,
    preview_index_record,
    preview_index_value,
)
from sico.service.preview_catalog import PreviewContent, preview_index, read_preview
from sico.service.workbench_data import WorkbenchService
from sico.storage.journal import SessionJournal
from test_circuit_preview import Client, receipt, svg

from cadai.circuit_preview import attach_svg_preview
from cadai.circuit_preview_files import preview_directory


class FakeReader:
    """One journal-shaped reader: workspace/.sico/ai/agent owns the session."""

    def __init__(self, workspace):
        self.root = workspace / ".sico/ai/agent"
        self.session_id = "preview"


def service_session(workspace, name="preview"):
    journal = SessionJournal(workspace, name)
    service = WorkbenchService()
    return journal, service


def stop(service):
    service.close()
    assert service.wait(5)


def test_index_lists_ready_artifact_and_manual_export_without_bytes(tmp_path):
    creation = attach_svg_preview(receipt(), Client(svg("auto")), tmp_path)
    artifact = creation["svg_preview"]["artifact"]
    manual = preview_directory(tmp_path) / "tb_inv_p3_g7.svg"
    manual.write_bytes(svg("manual"))
    index = preview_index(FakeReader(tmp_path))
    rows = {row.name: row for row in index.rows}
    assert set(rows) == {artifact["path"].rsplit("/", 1)[-1], "tb_inv_p3_g7.svg"}
    ready = rows[artifact["path"].rsplit("/", 1)[-1]]
    assert ready.registered and ready.sha256 == artifact["sha256"]
    assert ready.size == artifact["size_bytes"]
    assert ready.mtime > 0 and "path" not in dir(ready)
    listed = rows["tb_inv_p3_g7.svg"]
    assert not listed.registered and listed.sha256 == "" and listed.notice == ""
    assert index.notice == "" and index.truncated is False


def test_read_returns_verified_bytes_and_manual_notice(tmp_path):
    creation = attach_svg_preview(receipt(), Client(svg("auto")), tmp_path)
    artifact = creation["svg_preview"]["artifact"]
    name = artifact["path"].rsplit("/", 1)[-1]
    (preview_directory(tmp_path) / "tb_inv_p3_g7.svg").write_bytes(svg("manual"))
    reader = FakeReader(tmp_path)
    verified = read_preview(reader, name)
    assert verified.readable and verified.data == svg("auto") and verified.notice == ""
    assert verified.sha256 == artifact["sha256"] and verified.name == name
    listed = read_preview(reader, "tb_inv_p3_g7.svg")
    assert listed.readable and listed.data == svg("manual")
    assert "未登记" in listed.notice


def test_tampered_and_missing_previews_report_a_notice_without_raising(tmp_path):
    creation = attach_svg_preview(receipt(), Client(svg("auto")), tmp_path)
    artifact = creation["svg_preview"]["artifact"]
    path = preview_directory(tmp_path) / artifact["path"].rsplit("/", 1)[-1]
    path.write_bytes(svg("tampered"))
    reader = FakeReader(tmp_path)
    for name in (path.name, "missing.svg", "../auto.svg", None):
        result = read_preview(reader, name)
        assert isinstance(result, PreviewContent) and not result.readable
        assert "不可读或校验失败" in result.notice
        assert result.data == b""


def test_index_without_a_workspace_or_directory_keeps_the_viewer_empty(tmp_path):
    (tmp_path / "empty").mkdir()
    assert preview_index(FakeReader(tmp_path / "empty")).rows == ()
    assert preview_index(FakeReader(tmp_path / "empty")).notice == ""
    assert read_preview(FakeReader(tmp_path / "empty"), "auto.svg").notice


def test_workbench_source_lists_and_reads_through_the_journal(tmp_path):
    journal, service = service_session(tmp_path)
    try:
        creation = attach_svg_preview(receipt(), Client(svg("auto")), tmp_path)
        artifact = creation["svg_preview"]["artifact"]
        source = service.source(journal)
        index = source.preview_artifact("").result(timeout=10)
        assert [row.name for row in index.rows] == [artifact["path"].rsplit("/", 1)[-1]]
        content = source.preview_artifact(artifact["path"].rsplit("/", 1)[-1]).result(timeout=10)
        assert content.data == svg("auto") and content.sha256 == artifact["sha256"]
    finally:
        stop(service)
        journal.close()


def test_preview_wire_round_trip_verifies_the_asset_digest(tmp_path):
    creation = attach_svg_preview(receipt(), Client(svg("wire")), tmp_path)
    artifact = creation["svg_preview"]["artifact"]
    index = preview_index(FakeReader(tmp_path))
    record = preview_index_record(index)
    assert record["rows"][0]["sha256"] == artifact["sha256"]
    assert "path" not in record["rows"][0] and "data" not in record["rows"][0]
    content = read_preview(FakeReader(tmp_path), artifact["path"].rsplit("/", 1)[-1])
    wire = preview_content_record(content)
    assert wire["size"] == len(svg("wire")) and wire["sha256"] == artifact["sha256"]
    assert preview_content_value(wire).data == svg("wire")
    # A wire record whose digest no longer covers its bytes is refused.
    broken = dict(wire, sha256="0" * 64)
    try:
        preview_content_value(broken)
    except Exception as exc:
        assert "digest" in str(exc)
    else:
        raise AssertionError("a changed wire digest must be refused")
    index_wire = preview_index_record(preview_index(FakeReader(tmp_path)))
    rebuilt = preview_index_value(index_wire)
    assert [entry.name for entry in rebuilt.rows] == [artifact["path"].rsplit("/", 1)[-1]]
    assert "path" not in index_wire["rows"][0]


def test_source_work_runs_on_the_data_thread_and_rejects_a_closed_source(tmp_path):
    journal, service = service_session(tmp_path)
    try:
        source = service.source(journal)
        worker = threading.current_thread()
        threads = []

        def record():
            threads.append(threading.current_thread())
            return preview_index(FakeReader(tmp_path))

        assert source._preview_query("").rows == ()
        assert service._request(record).result(timeout=10).rows == ()
        assert threads and threads[0] is not worker
        source.close()
        with pytest.raises(ValueError, match="closed"):
            service._request(source._preview_query, "").result(timeout=10)
    finally:
        stop(service)
        journal.close()
