"""Projection transport, capability loss, and publication directory boundaries."""

import copy
import json
from pathlib import Path

import pytest
from template_extract_fixture import request
from template_native_content_fixture import guard, layout_rows
from template_qualification_fixture import collect_rows, routed_rows, symbol_rows
from test_template_standard_projection import source

from cadai.mcp import McpServer
from cadai.template_catalog import TemplateCatalog, call_template
from cadai.template_schema import TemplateError
from cadai.template_standard.codec import atomic, encode
from cadai.template_standard.identity import reference
from cadai.template_standard.package import load, publish
from cadai.template_standard.projection import compile_projection, project
from cadai.template_standard.reader import locate
from cadai.template_standard.replay import replay
from cadai.template_standard.validate import validate


def test_projection_mcp_offline_and_exact_match(tmp_path):
    result, catalog, _, _ = source(tmp_path)
    response = McpServer(None, workspace=tmp_path).handle(
        dict(
            id=1,
            method="tools/call",
            params=dict(
                name="project_circuit_template",
                arguments=dict(template_ref=result["template_ref"], level="L1"),
            ),
        )
    )
    assert not response["result"]["isError"]
    out = json.loads(response["result"]["content"][0]["text"])
    assert out["capabilities"]["topology-v1"] == "passed"
    Path(result["capture"]).unlink()
    match = call_template(
        "match_circuit_template",
        dict(template_ref=out["template_ref"], target_ref=result["template_ref"]),
        workspace=tmp_path,
    )
    assert match["matched"]
    with pytest.raises(TemplateError, match="must be raw"):
        project(catalog, out["template_ref"], "L2")


@pytest.mark.parametrize("level", ["L1", "L2", "L3"])
def test_layout_and_native_source_dependencies_are_never_public(tmp_path, level):
    result, catalog, item, policy = source(tmp_path, layout_rows())
    assert "layout" in item["documents"]
    out = project(catalog, result["template_ref"], level, master_policy=policy)
    stored = load(out["package"])
    assert "layout" not in stored["templates"]["main"]
    assert out["capabilities"]["layout-reference-v1"] == "not_applicable"
    assert all(d["kind"] in {"file", "master"} for d in stored["package"]["dependencies"].values())
    assert all(r["kind"] == "private_evidence" for r in stored["tables"]["file"]["items"].values())


def test_symbol_only_projection_and_l1_no_asset(tmp_path):
    result, _ = collect_rows(tmp_path, symbol_rows())
    catalog = TemplateCatalog(workspace=tmp_path)
    out = project(catalog, result["template_ref"], "L2")
    assert out["capabilities"]["symbol-reference-v1"] == "passed"
    with pytest.raises(TemplateError, match="no applicable"):
        project(catalog, result["template_ref"], "L1")


def test_existing_gap_cannot_be_removed_to_qualify(tmp_path):
    rows = guard(routed_rows())
    endpoint = next(r for r in rows if r["kind"] == "instance_terminal")
    next(r for r in rows if r["kind"] == "net" and r["name"] == endpoint["net"])[
        "instance_terminal_count"
    ] -= 1
    endpoint["net"] = None
    # Guard must be recomputed after a captured row changes.
    result, catalog, _, policy = source(tmp_path, guard(rows))
    out = project(catalog, result["template_ref"], "L2", master_policy=policy)
    assert out["capabilities"]["topology-v1"] == "unknown"
    assert out["capabilities"]["routing-reference-v1"] == "unknown"


def test_permission_unknown_is_not_promoted(tmp_path):
    args = request()
    args.pop("reuse")
    result, _ = collect_rows(tmp_path, guard(routed_rows()), args)
    catalog = TemplateCatalog(workspace=tmp_path)
    out = project(catalog, result["template_ref"], "L1")
    item, _ = locate(catalog, out["template_ref"])
    assert item["entry"]["use"]["reuse"] == "unknown"
    assert out["capabilities"]["whole-reuse-v1"] == "not_applicable"


@pytest.mark.parametrize("fault", ["private", "payload", "raw"])
def test_public_output_cannot_mix_private_content(tmp_path, fault):
    result, catalog, item, _ = source(tmp_path)
    out = tmp_path / "public"
    out.mkdir()
    if fault == "private":
        out = catalog.root
    elif fault == "payload":
        (out / "secret.txt").write_text("private")
    else:
        publish(out, item["package"], item["tables"], item["templates"], roots={})
    with pytest.raises(TemplateError, match="public projection output"):
        project(catalog, result["template_ref"], "L1", output=out)


def test_failed_sanitization_never_stages_private_bytes(tmp_path):
    _, _, item, policy = source(tmp_path)
    package, tables, docs, _, _ = compile_projection(item, "L1", policy, None)
    tables["sources"]["items"]["capture"]["summary"] = "DO_NOT_WRITE_SECRET"
    with pytest.raises(TemplateError, match="closure"):
        publish(tmp_path / "public", package, tables, {"main": docs})
    assert list((tmp_path / "public").iterdir()) == []


def test_manifest_cannot_smuggle_unused_hashed_payload(tmp_path):
    result, catalog, _, _ = source(tmp_path)
    out = project(catalog, result["template_ref"], "L1")
    root = Path(out["package"])
    manifest = json.loads((root / "package.json").read_text())
    digest = atomic(root / "private.json", dict(secret="private"))
    manifest["files"]["private.json"] = digest
    atomic(root / "package.json", manifest)
    with pytest.raises(TemplateError, match="unused"):
        load(root)


def test_sharded_projection_and_unknown_gap_receipt_roundtrip(tmp_path):
    _, _, item, policy = source(tmp_path)
    raw = copy.deepcopy(item)
    raw["documents"]["collection"]["gaps"] = {
        "private" + str(i): dict(
            part="topology.json",
            object="private",
            pointer="",
            code="unsupported_feature",
            operations=["topology-v1"],
            reason="private",
            action="complete_evidence",
        )
        for i in range(140)
    }
    package, tables, docs, _, _ = compile_projection(raw, "L1", policy, None)
    path = publish(tmp_path / "public", package, tables, {"main": docs}, roots={})
    assert len(load(path)["templates"]["main"]["collection"]["gaps"]) == 140
    assert list(path.glob("template/collection.gaps.*.json"))


def test_corrupt_private_proof_is_an_error_not_missing_evidence(tmp_path):
    result, catalog, _, _ = source(tmp_path)
    Path(result["capture"]).write_bytes(b"{}\n")
    with pytest.raises(TemplateError):
        project(catalog, result["template_ref"], "L1")


def test_resource_dependency_must_bind_its_own_file(tmp_path):
    _, _, item, _ = source(tmp_path)
    tables = copy.deepcopy(item["tables"])
    tables["file"]["items"]["other"] = copy.deepcopy(tables["file"]["items"]["capture"])
    with pytest.raises(TemplateError, match="own file_ref"):
        validate(item["package"], tables, item["templates"])


def test_projection_recomputes_receipt_without_changing_raw(tmp_path):
    result, _, item, policy = source(tmp_path)
    record = replay(item)[0]
    refs = []
    for level in ("L1", "L2", "L3"):
        package, tables, docs, _, report = compile_projection(item, level, policy, record)
        refs.append(reference(package, tables, docs, "main"))
        assert (
            json.loads(report)["validator"]
            != item["documents"]["qualification"]["checks"]["topology-v1"]["validator"]
        )
        assert b"source_name" not in encode(docs)
    assert len(set(refs + [result["template_ref"]])) == 4


def test_core_contract_and_private_group_ids_survive_projection(tmp_path):
    from template_reuse_input_fixture import series_capture

    rows, args = series_capture()
    result, _ = collect_rows(tmp_path, rows, args)
    catalog = TemplateCatalog(workspace=tmp_path)
    out = project(catalog, result["template_ref"], "L1")
    item, _ = locate(catalog, out["template_ref"])
    assert out["capabilities"]["core-reuse-v1"] == "passed"
    assert out["capabilities"]["whole-reuse-v1"] == "not_applicable"
    contract = item["documents"]["reuse"]
    assert set(contract["optional_groups"]) == {"g0"}
    assert contract["boundaries"] and "load" not in encode(contract).decode()
    assert "rename" not in contract["allowed_rules"]


def test_dropped_classification_attributes_downgrade_instead_of_widening(tmp_path):
    args = request()
    args["classifications"]["M0"]["attributes"] = dict(voltage_domain="secret")
    result, _ = collect_rows(tmp_path, guard(routed_rows()), args)
    catalog = TemplateCatalog(workspace=tmp_path)
    out = project(catalog, result["template_ref"], "L1")
    assert out["capabilities"]["topology-v1"] == "unknown"
    assert out["capabilities"]["whole-reuse-v1"] == "unknown"
    item, _ = locate(catalog, out["template_ref"])
    assert b"secret" not in encode(item["documents"])
