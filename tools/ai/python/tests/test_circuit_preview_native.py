"""Native SVG export to a private .cad file and failure isolation."""

import hashlib
import json
import os
from pathlib import Path

import pytest
from circuit_create_fixture import fixture
from circuit_create_support import sample_master, session
from test_circuit_rc_gui import mcp_call

pytestmark = pytest.mark.skipif(
    os.environ.get("SICO_AI_RUN_GUI_TESTS") != "1", reason="licensed SVG preview acceptance"
)


def test_export_to_cad_file_preserves_created_oa(tmp_path):
    with session(tmp_path) as (bridge, server, _):
        data = {cell: sample_master(bridge, cell) for cell in ("res", "cap")}
        mcp_call(server, "begin_circuit_task", dict(
            task_id="svg", presentation="background", choice_confirmed=True))
        args = {**fixture(data, "svg_file"), "task_ref": "task:svg", "request_id": "svg"}
        prepared = mcp_call(server, "prepare_circuit_creation", args)
        assert prepared["ok"], prepared
        request = dict(prepare_ref=prepared["prepare_ref"],
                       geometry_plan_digest=args["geometry_plan_digest"], request_id="svg")
        bridge.skill("svgWindows=hiGetWindowList('all)")
        created = mcp_call(server, "create_circuit_from_plan", request)
        assert created["ok"] and created["saved"] and created["readback_verified"], created
        preview = created["svg_preview"]
        assert preview["status"] == "ready" and preview["acceptance_blocking"] is False
        path = Path(preview["artifact"]["path"])
        assert path.read_text().startswith("<svg")
        assert path.is_relative_to(tmp_path / ".sico" / "ai" / "circuit_previews")
        assert "data" not in preview and "context" not in preview
        assert bridge.skill("equal(svgWindows hiGetWindowList('all))") == "t"
        assert mcp_call(server, "create_circuit_from_plan", request) == created
        oa = tmp_path / "library/svg_file/schematic/sch.oa"
        before = hashlib.sha256(oa.read_bytes()).hexdigest()
        inspected = mcp_call(server, "inspect_created_circuit", dict(circuit_ref="created:svg"))
        assert inspected["ok"] and inspected["svg_preview"]["status"] == "ready"
        assert inspected["svg_preview"] == preview
        native = json.loads(bridge.skill('aiCreateInspect("created:svg")'))
        assert native["svg_preview"]["path"] == str(path)
        assert "<svg" not in json.dumps(native)
        assert hashlib.sha256(oa.read_bytes()).hexdigest() == before
        # New retained receipt, saved target: fail the actual exporter with a
        # missing parent directory, without patching Cadence's protected function.
        bridge.skill('svgOriginal=aiCreatedCircuits["created:svg"] '
                     'svgBad=makeTable("svgBad" nil) '
                     "foreach(k list('payload 'saved 'verified 'snapshot 'previewWorkspace) "
                     "svgBad[k]=svgOriginal[k]) "
                     'aiCreatedCircuits["created:bad-svg"]=svgBad')
        root = tmp_path / ".sico/ai/circuit_previews"
        # The original project binding, not a later CIW working directory, owns export.
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        detached = json.loads(bridge.skill(
            "unwindProtect(progn(changeWorkingDir(" + json.dumps(str(elsewhere)) + ") "
            'aiCrExportSvg("created:bad-svg" '
            + json.dumps(str(root / ("b" * 32 + ".svg"))) + ")) "
            "changeWorkingDir(" + json.dumps(str(tmp_path)) + "))"))
        assert detached["status"] == "ready", detached
        bridge.skill("svgBad['svgPreviewReply]=nil")
        root.rename(root.with_name("previews-moved"))
        try:
            failure = json.loads(bridge.skill('aiCrExportSvg("created:bad-svg" '
                                 + json.dumps(str(root / ("a" * 32 + ".svg"))) + ')'))
            assert not failure["ok"] and failure["status"] == "export_failed"
            assert hashlib.sha256(oa.read_bytes()).hexdigest() == before
            assert bridge.skill("equal(svgWindows hiGetWindowList('all))") == "t"
        finally:
            root.with_name("previews-moved").rename(root)
