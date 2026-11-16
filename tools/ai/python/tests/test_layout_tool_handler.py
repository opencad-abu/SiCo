import json
from pathlib import Path

from test_layout_backend_host import manifest, request

from cadai.layout.service import LayoutService
from cadai.mcp_layout_handler import dispatch_layout


def service(tmp_path):
    root = tmp_path / "project"
    directory = root / ".sico/layout/backends/fake-layout"
    directory.mkdir(parents=True)
    (directory / "backend.json").write_text(
        json.dumps(manifest(tmp_path), indent=2) + "\n", encoding="utf-8"
    )
    return LayoutService(root)


def core_request(tmp_path):
    value = request(tmp_path)
    return {
        key: value[key]
        for key in ("action", "source", "process_view_ref", "inputs", "constraints",
                    "outputs", "options", "resources", "timeout_sec")
    }


def test_layout_handler_lists_previews_and_runs_job(tmp_path):
    current = service(tmp_path)
    try:
        ok, listed = dispatch_layout("list_layout_backends", {}, get_service=lambda: current)
        assert ok and listed["backends"][0]["id"] == "fake-layout"
        ok, preview = dispatch_layout("preview_layout_job", {
            "backend_id": "fake-layout", "request": core_request(tmp_path),
        }, get_service=lambda: current)
        assert ok and preview["validation"]["accepted"] is True
        ok, started = dispatch_layout("start_layout_job", {
            "backend_id": "fake-layout", "request": core_request(tmp_path),
        }, get_service=lambda: current)
        assert ok
        ok, status = dispatch_layout("get_layout_job", {
            "job_id": started["job_id"],
        }, get_service=lambda: current)
        assert ok and status["state"] == "succeeded"
        assert status["result"]["artifacts"][0]["role"] == "layout"
        layer_map = tmp_path / "layer.map"
        layer_map.write_text("M1 61 0\n", encoding="utf-8")
        ok, plan = dispatch_layout("plan_layout_import", {
            "job_id": started["job_id"],
            "target_library": "WORK",
            "target_cell": "FAKE",
            "target_view": "layout_pnr",
            "layer_map": str(layer_map),
        }, get_service=lambda: current)
        assert ok and Path(plan["plan"]["skill_file"]).is_file()
        ok, status = dispatch_layout("get_layout_import_status", {
            "plan_file": plan["plan"]["plan_file"],
        }, get_service=lambda: current)
        assert ok and status["status"]["state"] == "planned"
    finally:
        current.close()


def test_layout_handler_reports_missing_backend(tmp_path):
    current = service(tmp_path)
    try:
        ok, detail = dispatch_layout("preview_layout_job", {
            "backend_id": "missing", "request": core_request(tmp_path),
        }, get_service=lambda: current)
        assert ok is False
        assert detail["code"] == "layout_backend_error"
    finally:
        current.close()


def test_layout_handler_reports_invalid_import_plan(tmp_path):
    current = service(tmp_path)
    try:
        ok, started = dispatch_layout("start_layout_job", {
            "backend_id": "fake-layout", "request": core_request(tmp_path),
        }, get_service=lambda: current)
        assert ok
        ok, status = dispatch_layout("get_layout_job", {
            "job_id": started["job_id"],
        }, get_service=lambda: current)
        assert ok and status["state"] == "succeeded"
        ok, detail = dispatch_layout("plan_layout_import", {
            "job_id": started["job_id"],
            "target_library": "WORK",
            "target_cell": "FAKE",
            "target_view": "layout_pnr",
            "layer_map": str(tmp_path / "missing.map"),
        }, get_service=lambda: current)
        assert ok is False
        assert detail["code"] == "layout_import_plan_error"
        assert "layer_map" in detail["message"]
    finally:
        current.close()
