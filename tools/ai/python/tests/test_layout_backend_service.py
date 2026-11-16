import json
from pathlib import Path

import pytest
from test_layout_backend_host import manifest, request

from cadai.layout.registry import ManifestError
from cadai.layout.service import LayoutService, LayoutServiceError


def service(tmp_path):
    root = tmp_path / "project"
    backend_dir = root / ".sico/layout/backends/fake-layout"
    backend_dir.mkdir(parents=True)
    (backend_dir / "backend.json").write_text(
        json.dumps(manifest(tmp_path), indent=2) + "\n", encoding="utf-8"
    )
    return LayoutService(root)


def test_service_discovers_and_returns_safe_backend_summary(tmp_path):
    current = service(tmp_path)
    listed = current.list_backends()
    assert listed["errors"] == []
    assert [item["id"] for item in listed["backends"]] == ["fake-layout"]
    info = current.get_backend("fake-layout")
    assert info["capabilities"]["actions"]["synthesize"]["produces"] == ["layout"]
    assert "transport" not in info


def test_service_preview_and_start_status_collect(tmp_path):
    current = service(tmp_path)
    preview = current.preview("fake-layout", request(tmp_path))
    assert preview["accepted"] is True

    started = current.start("fake-layout", request(tmp_path))
    assert started["job_id"].startswith("layout-")
    status = current.status(started["job_id"])
    assert status["state"] == "succeeded"
    assert status["collected"] is True
    assert status["result"]["artifacts"][0]["role"] == "layout"
    repeated = current.status(started["job_id"])
    assert repeated["result"] == status["result"]

    layer_map = tmp_path / "layer.map"
    layer_map.write_text("M1 61 0\n", encoding="utf-8")
    plan = current.plan_import(
        started["job_id"],
        target_library="WORK",
        target_cell="FAKE",
        target_view="layout_pnr",
        layer_map=str(layer_map),
    )
    assert plan["target"]["cell"] == "FAKE"
    assert Path(plan["skill_file"]).is_file()
    assert "xstInSetField" in Path(plan["skill_file"]).read_text(encoding="utf-8")
    import_status = current.import_status(plan["plan_file"])
    assert import_status["state"] == "planned"
    Path(plan["completion"]["marker_file"]).write_text(
        "completed\n", encoding="utf-8"
    )
    assert current.import_status(plan["plan_file"])["state"] == (
        "completion_marker_present"
    )


def test_service_cancel_running_job(tmp_path):
    current = service(tmp_path)
    started = current.start("fake-layout", request(tmp_path, options={"fake_hold": True}))
    assert current.status(started["job_id"])["state"] == "running"
    assert current.cancel(started["job_id"])["state"] == "cancelled"
    assert current.status(started["job_id"])["state"] == "cancelled"


def test_service_reports_invalid_manifest_and_unknown_backend(tmp_path):
    current = service(tmp_path)
    bad = current.backends_root / "bad" / "backend.json"
    bad.parent.mkdir(parents=True)
    bad.write_text('{"schema":"wrong"}\n', encoding="utf-8")
    listed = current.list_backends()
    assert any("bad" in row["path"] for row in listed["errors"])
    with pytest.raises(LayoutServiceError):
        current.status("layout-missing")
    with pytest.raises(ManifestError):
        current.get_backend("missing")
