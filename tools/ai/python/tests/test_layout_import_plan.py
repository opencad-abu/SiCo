import hashlib
import json
from pathlib import Path

import pytest

from cadai.layout.import_plan import (
    ImportPlanError,
    _approval_binding,
    _canonical_digest,
    build_import_plan,
    import_status,
    verify_import_plan,
)
from cadai.skill_check import check_source


def result(workspace, *, content=b"GDS\n", name="layout.gds", fmt="gds",
           artifacts=None):
    output = workspace / "output"
    output.mkdir(parents=True, exist_ok=True)
    path = output / name
    path.write_bytes(content)
    row = {
        "role": "layout",
        "path": "output/" + name,
        "format": fmt,
        "sha256": hashlib.sha256(content).hexdigest(),
        "size": len(content),
    }
    if fmt is None:
        row.pop("format")
    return {
        "schema": "sico.layout.result.v1",
        "job_id": "layout-" + "a" * 32,
        "state": "succeeded",
        "artifacts": artifacts if artifacts is not None else [row],
    }


def layer_map(tmp_path, text="M1 61 0\n"):
    path = tmp_path / "layer.map"
    path.write_text(text, encoding="utf-8")
    return path


def build(tmp_path, value=None, mapping=None, **overrides):
    workspace = tmp_path / "job"
    workspace.mkdir(parents=True, exist_ok=True)
    value = result(workspace) if value is None else value
    mapping = layer_map(tmp_path) if mapping is None else mapping
    arguments = {
        "workspace": workspace,
        "target_library": "SicoLayout_test",
        "target_cell": "TOP",
        "target_view": "layout",
        "layer_map": str(mapping),
        "expected_job_id": value["job_id"],
        **overrides,
    }
    return workspace, build_import_plan(value, **arguments)


def test_import_plan_binds_inputs_and_generates_create_only_skill(tmp_path):
    workspace, plan = build(tmp_path)
    assert plan["plan_id"].startswith("import-")
    assert plan["source"]["format"] == "gds"
    assert plan["source"]["sha256"] == hashlib.sha256(b"GDS\n").hexdigest()
    assert plan["layer_map"]["sha256"] == hashlib.sha256(b"M1 61 0\n").hexdigest()
    assert plan["policy"] == {
        "target": "create_only",
        "write_mode": "noOverwrite",
        "execution": "serialized_per_virtuoso_session",
        "requires_review": True,
    }
    assert len(plan["approval_plan_digest"]) == 64

    skill_path = Path(plan["skill_file"])
    skill = skill_path.read_text(encoding="utf-8")
    assert check_source(skill.encode(), source_name=str(skill_path))["ok"] is True
    assert "ddGetObj(\"SicoLayout_test\" \"TOP\" \"layout\")" in skill
    assert 'xstInSetField("writeMode" "noOverwrite")' in skill
    assert 'xstInSetField("summaryFile"' in skill
    assert f"xstInOnCompletionCB(\"r\" '{plan['completion']['callback']})" in skill
    assert "xstInOnCompletionCB(\"d\"" not in skill
    assert "xstInDoTranslate()" in skill

    checked = verify_import_plan(plan["plan_file"], workspace=workspace)
    assert checked["plan_id"] == plan["plan_id"]
    assert checked["approval_plan_digest"] == plan["approval_plan_digest"]


def test_verify_import_plan_rejects_changed_artifacts(tmp_path):
    workspace, plan = build(tmp_path)
    source = Path(plan["source"]["path"])
    source.write_bytes(b"CHANGED\n")
    with pytest.raises(ImportPlanError, match="artifact changed"):
        verify_import_plan(plan["plan_file"], workspace=workspace)

    workspace, plan = build(tmp_path / "next")
    mapping = Path(plan["layer_map"]["path"])
    mapping.write_text("M2 62 0\n", encoding="utf-8")
    with pytest.raises(ImportPlanError, match="layer map changed"):
        verify_import_plan(plan["plan_file"], workspace=workspace)

    workspace, plan = build(tmp_path / "skill")
    Path(plan["skill_file"]).write_text("(get_context)\n", encoding="utf-8")
    with pytest.raises(ImportPlanError, match="SKILL changed"):
        verify_import_plan(plan["plan_file"], workspace=workspace)


def test_import_plan_rejects_self_unregistering_completion_callback(tmp_path):
    workspace, plan = build(tmp_path)
    skill_path = Path(plan["skill_file"])
    skill = skill_path.read_text(encoding="utf-8")
    assert 'xstInOnCompletionCB("d"' not in skill
    skill += '\n; legacy unsafe cleanup\nxstInOnCompletionCB("d" \'sicoLegacyDone)\n'
    skill_path.write_text(skill, encoding="utf-8")
    plan_path = Path(plan["plan_file"])
    data = json.loads(plan_path.read_text(encoding="utf-8"))
    data["skill_sha256"] = hashlib.sha256(skill_path.read_bytes()).hexdigest()
    data["approval_plan_digest"] = _canonical_digest(_approval_binding(data))
    plan_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with pytest.raises(ImportPlanError, match="completion callback"):
        verify_import_plan(str(plan_path), workspace=workspace)
    with pytest.raises(ImportPlanError, match="completion callback"):
        import_status(str(plan_path), workspace=workspace)


def test_verify_import_plan_rejects_completed_or_tampered_plan(tmp_path):
    workspace, plan = build(tmp_path)
    Path(plan["completion"]["marker_file"]).write_text("completed\n", encoding="utf-8")
    with pytest.raises(ImportPlanError, match="already exists"):
        verify_import_plan(plan["plan_file"], workspace=workspace)

    workspace, plan = build(tmp_path / "tamper")
    plan_path = Path(plan["plan_file"])
    value = json.loads(plan_path.read_text(encoding="utf-8"))
    value["target"]["cell"] = "OTHER"
    plan_path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ImportPlanError, match="approval digest"):
        verify_import_plan(str(plan_path), workspace=workspace)


def test_import_status_reports_marker_and_summary(tmp_path):
    workspace, plan = build(tmp_path)
    status = import_status(plan["plan_file"], workspace=workspace)
    assert status["state"] == "planned"
    assert status["completion"]["marker_present"] is False
    assert status["completion"]["summary_present"] is False
    Path(plan["source"]["path"]).write_bytes(b"changed after dispatch\n")
    assert import_status(plan["plan_file"], workspace=workspace)["state"] == "planned"
    Path(plan["completion"]["marker_file"]).write_text(
        "completed\n", encoding="utf-8"
    )
    Path(plan["completion"]["summary_file"]).write_text(
        "summary\n", encoding="utf-8"
    )
    status = import_status(plan["plan_file"], workspace=workspace)
    assert status["state"] == "completion_marker_present"
    assert status["completion"]["marker_text"] == "completed\n"
    assert status["completion"]["summary_present"] is True
    assert status["approval_plan_digest"] == plan["approval_plan_digest"]


def test_import_plan_rejects_invalid_layer_maps(tmp_path):
    workspace = tmp_path / "job"
    workspace.mkdir()
    value = result(workspace)
    missing = tmp_path / "missing.map"
    with pytest.raises(ImportPlanError, match="absolute"):
        build_import_plan(value, workspace=workspace, target_library="LIB",
                          target_cell="TOP", target_view="layout",
                          layer_map="layer.map")
    with pytest.raises(ImportPlanError, match="missing"):
        build_import_plan(value, workspace=workspace, target_library="LIB",
                          target_cell="TOP", target_view="layout",
                          layer_map=str(missing))
    empty = tmp_path / "empty.map"
    empty.write_bytes(b"")
    with pytest.raises(ImportPlanError, match="empty"):
        build_import_plan(value, workspace=workspace, target_library="LIB",
                          target_cell="TOP", target_view="layout",
                          layer_map=str(empty))
    bad = tmp_path / "bad.map"
    bad.write_bytes(b"M1\x0061 0\n")
    with pytest.raises(ImportPlanError, match="NUL"):
        build_import_plan(value, workspace=workspace, target_library="LIB",
                          target_cell="TOP", target_view="layout",
                          layer_map=str(bad))


@pytest.mark.parametrize(
    ("artifacts", "job_id", "message"),
    [
        ([], "layout-" + "a" * 32, "exactly one"),
        ([{"role": "route", "path": "output/layout.gds", "sha256": "x"}],
         "layout-" + "a" * 32, "exactly one"),
        (None, "layout-" + "b" * 32, "job_id differs"),
    ],
)
def test_import_plan_rejects_invalid_results(tmp_path, artifacts, job_id, message):
    workspace = tmp_path / "job"
    workspace.mkdir()
    value = result(workspace, artifacts=artifacts)
    with pytest.raises(ImportPlanError, match=message):
        build_import_plan(
            value, workspace=workspace, target_library="LIB", target_cell="TOP",
            target_view="layout", layer_map=str(layer_map(tmp_path)),
            expected_job_id=job_id,
        )


def test_import_plan_rejects_artifact_escape_and_non_gds(tmp_path):
    workspace = tmp_path / "job"
    workspace.mkdir()
    value = result(workspace)
    value["artifacts"][0]["path"] = "../layout.gds"
    with pytest.raises(ImportPlanError, match="escapes"):
        build_import_plan(value, workspace=workspace, target_library="LIB",
                          target_cell="TOP", target_view="layout",
                          layer_map=str(layer_map(tmp_path)))
    oasis = result(workspace, name="layout.oas", fmt="oasis")
    with pytest.raises(ImportPlanError, match="GDS"):
        build_import_plan(oasis, workspace=workspace, target_library="LIB",
                          target_cell="TOP", target_view="layout",
                          layer_map=str(layer_map(tmp_path)))
    changed = result(workspace)
    changed["artifacts"][0]["sha256"] = "0" * 64
    with pytest.raises(ImportPlanError, match="digest differs"):
        build_import_plan(changed, workspace=workspace, target_library="LIB",
                          target_cell="TOP", target_view="layout",
                          layer_map=str(layer_map(tmp_path)))


def test_import_plan_rejects_unsafe_target_names(tmp_path):
    workspace = tmp_path / "job"
    workspace.mkdir()
    value = result(workspace)
    with pytest.raises(ImportPlanError, match="target_cell"):
        build_import_plan(value, workspace=workspace, target_library="LIB",
                          target_cell="bad cell", target_view="layout",
                          layer_map=str(layer_map(tmp_path)))
