"""Whole extraction is public, immutable, explicit, and cannot bypass source qualification."""

import copy
import json
from pathlib import Path

import pytest
from template_adapt_fixture import target_case
from template_extract_fixture import CaptureClient, capture_rows, recount, request
from test_template_circuit_transport import rpc, unpack

from cadai.circuit_spec_schema import CircuitSpecError
from cadai.mcp import McpServer
from cadai.template_build import make_template
from cadai.template_capture import read_capture
from cadai.template_catalog import TemplateCatalog, call_template
from cadai.template_schema import (
    CAPTURE_SCHEMA,
    CAPTURE_SCHEMA_V2,
    SCHEMA,
    TemplateError,
    arguments,
    capture_skill,
)
from cadai.template_source_qualification import require_qualified


def extract(tmp_path, rows=None, args=None):
    client = CaptureClient(tmp_path / "spool", rows or capture_rows())
    server = McpServer(client, workspace=tmp_path)
    response = rpc(server, "extract_circuit_templates", args or request())
    assert "error" not in response, response
    result = json.loads(response["result"]["content"][0]["text"])
    assert response["result"]["isError"] == (not result["ok"])
    return client, server, result


def test_public_extract_query_core_prepare_and_idempotence(tmp_path):
    client, server, result = extract(tmp_path)
    assert result["ok"] and result["schema_version"] == "0.1.0"
    assert result["reuse"]["status"] == "qualified" and result["reuse"]["issues"] == []
    assert result["reuse"]["template_ref"] == result["template_ref"]
    assert client.calls[0]["capture_schema"] == CAPTURE_SCHEMA_V2
    record = TemplateCatalog(workspace=tmp_path).get(result["template_ref"])
    assert require_qualified(record) and result["template_ref"].startswith("ctpl_")
    contract = record["reuse_contract"]
    assert contract["core_devices"] == ["d0", "d1"]
    assert {(r["device"], r["terminal"]) for r in contract["core_connections"]} == {
        (d, p) for d in ["d0", "d1"] for p in ["D", "G"]}
    assert not contract["optional_groups"] and not contract["boundary_terminals"]
    listed = unpack(rpc(server, "query_circuit_templates", dict(tier="private")))
    assert [r["template_ref"] for r in listed["items"]] == [result["template_ref"]]
    target = target_case(record)
    prepared = unpack(rpc(server, "prepare_circuit_template_plan", {
        k: target[k] for k in ["spec", "bindings", "template_ref", "mapping", "allowed_rule_refs"]
    } | dict(options=dict(match_mode="core", allowed_tiers=["private"], return_alternatives=0))))
    assert prepared["status"] == "needs_geometry", prepared
    assert prepared["best_match"]["template_ref"] == record["template_ref"]
    paths = [Path(result["package"]) / "package.json", Path(result["capture"])]
    before = [(p.read_bytes(), p.stat().st_mtime_ns) for p in paths]
    assert unpack(rpc(server, "extract_circuit_templates", request())) == result
    assert [(p.read_bytes(), p.stat().st_mtime_ns) for p in paths] == before


def test_old_extraction_preserves_exact_record_and_new_intent_does_not_overwrite(tmp_path):
    args = request()
    args.pop("reuse")
    client, server, result = extract(tmp_path, args=args)
    assert "reuse" not in result and result["schema_version"] == "0.1.0"
    assert client.calls[0]["capture_schema"] == CAPTURE_SCHEMA_V2
    expected = make_template(read_capture(client.path), classifications=args["classifications"])
    catalog = TemplateCatalog(workspace=tmp_path)
    actual = catalog.get(result["template_ref"])
    assert actual["topology"] == expected["topology"]
    assert actual["assets"] == expected["assets"]
    before = (Path(result["package"]) / "package.json").read_bytes()
    qualified = unpack(rpc(server, "extract_circuit_templates", request()))
    assert qualified["template_ref"] != result["template_ref"]
    assert (Path(result["package"]) / "package.json").read_bytes() == before
    actual = catalog.get(result["template_ref"])
    assert actual["topology"] == expected["topology"]
    assert actual["assets"] == expected["assets"]


@pytest.mark.parametrize("fault", ["classification", "inheritance", "hierarchy", "geometry"])
def test_failed_reuse_keeps_only_reference_with_actionable_issue(tmp_path, fault):
    rows, args = capture_rows(), request()
    if fault == "classification":
        args["classifications"].pop("M1")
    elif fault == "inheritance":
        rows[1]["electrical_observation"]["views"][0]["terminal_expressions"] = ["vdd!"]
    elif fault == "hierarchy":
        rows[2]["schematic_available"] = True
        args["classifications"].pop("M0")
    else:
        next(r for r in rows if r["kind"] == "instance_geometry")["observation"] = dict(
            ok=False, code="unavailable", message="missing geometry")
    client, _, result = extract(tmp_path, rows=rows, args=args)
    assert not result["ok"] and result["stage"] == "stored_reference"
    assert result["reuse"]["template_ref"] is None and result["reuse"]["issues"]
    if fault == "classification":
        assert result["reuse"]["status"] == "needs_classification"
        assert result["reuse"]["issues"][0]["object_ref"] == "M1"
    else:
        assert result["reuse"]["status"] == "unavailable"
    record = TemplateCatalog(workspace=tmp_path).get(result["template_ref"])
    assert record["schema_version"] == SCHEMA and "reuse_contract" not in record
    assert Path(result["capture"]).read_bytes() == client.path.read_bytes()


def test_rename_permission_changes_content_and_is_not_implicitly_granted(tmp_path):
    _, server, original = extract(tmp_path)
    args = request()
    args["reuse"]["allow_rename"] = False
    result = unpack(rpc(server, "extract_circuit_templates", args))
    assert original["template_ref"] != result["template_ref"]
    record = TemplateCatalog(workspace=tmp_path).get(result["template_ref"])
    assert record["reuse_contract"]["allowed_rule_refs"] == []
    from cadai.template_adaptation import adapt
    from cadai.template_rules import rule_ref

    target = target_case(record)
    target["spec"]["nets"][0]["name"] = "renamed"
    old = next(iter(target["mapping"]["net_map"].values()))
    for instance in target["spec"]["instances"]:
        instance["connections"] = {pin: "renamed" if net == old else net
                                   for pin, net in instance["connections"].items()}
    for port in target["spec"]["ports"]:
        if port["net"] == old:
            target["mapping"]["port_map"][port["name"]] = "renamed"
            port.update(name="renamed", net="renamed")
    for key, net in target["mapping"]["net_map"].items():
        if net == old:
            target["mapping"]["net_map"][key] = "renamed"
    with pytest.raises(CircuitSpecError, match="needs_adaptation"):
        adapt(record, target["spec"], target["bindings"], target["mapping"], [rule_ref("rename")])


@pytest.mark.parametrize("change", [dict(assets=["symbol"]), dict(capture_schema=CAPTURE_SCHEMA),
    dict(reuse=dict(mode="whole")), dict(reuse=dict(mode="whole", allow_rename=1)),
    dict(reuse=dict(mode="optional", allow_rename=True))])
def test_invalid_reuse_request_fails_before_live_read(tmp_path, change):
    client = CaptureClient(tmp_path / "spool", capture_rows())
    with pytest.raises(TemplateError):
        call_template("extract_circuit_templates", request() | change,
                      workspace=tmp_path, client=client)
    assert not client.calls
    assert not (tmp_path / ".sico/ai/circuit_templates").exists()


@pytest.mark.parametrize("fault", ["source", "assets", "semantics", "counts", "dependency"])
def test_invalid_capture_cannot_publish_reference_or_reusable(tmp_path, fault):
    rows = copy.deepcopy(capture_rows())
    if fault == "source":
        rows[1]["cell"] = "wrong"
    elif fault == "assets":
        rows[0]["assets"].append("symbol")
    elif fault == "semantics":
        rows[1]["classification_semantics"] = "old"
    elif fault == "counts":
        rows[-2]["counts"]["instance"] += 1
    else:
        next(r for r in rows if r["kind"] == "dependency_guard")["after"] = "changed"
    client = CaptureClient(tmp_path / "spool", rows)
    with pytest.raises(TemplateError):
        call_template("extract_circuit_templates", request(), workspace=tmp_path, client=client)
    assert not list((tmp_path / ".sico/ai/circuit_templates").rglob("*.sqlite3"))


def test_missing_classification_report_is_bounded(tmp_path):
    rows, args = capture_rows(), request()
    # Add connected observed devices to isolate classification response bounds.
    source = rows[2]
    geo = next(r for r in rows if r["kind"] == "instance_geometry")
    for index in range(12):
        name = "extra" + str(index)
        rows[-2:-2] = [dict(source, name=name), dict(geo, instance=name),
                        dict(kind="instance_terminal", instance=name, name="D", net="Y"),
                        dict(kind="instance_terminal", instance=name, name="G", net="A")]
    for row in rows:
        if row["kind"] == "net":
            row["instance_terminal_count"] += 12
    rows[1]["electrical_observation"]["views"][0]["instance_count"] = 14
    _, _, result = extract(tmp_path, rows=recount(rows), args=args)
    assert result["reuse"]["issue_count"] == 12
    assert len(result["reuse"]["issues"]) == 8


def test_old_collector_v1_cannot_satisfy_whole_reuse(tmp_path):
    from test_template_classification import observed_rows

    _, _, result = extract(tmp_path, rows=observed_rows())
    assert not result["ok"] and result["reuse"]["template_ref"] is None
    assert result["reuse"]["issues"][0]["code"] == "capture_v2_required"


def test_failed_reuse_can_be_corrected_with_evidence_without_overwriting_reference(tmp_path):
    args = request()
    args["classifications"].pop("M1")
    _, server, partial = extract(tmp_path, args=args)
    original = (Path(partial["package"]) / "package.json").read_bytes()
    complete = unpack(rpc(server, "extract_circuit_templates", request()))
    assert complete["reuse"]["status"] == "qualified"
    assert complete["template_ref"] != partial["template_ref"]
    assert (Path(partial["package"]) / "package.json").read_bytes() == original


def test_private_reuse_publication_detects_existing_conflicts(tmp_path, monkeypatch):

    shared = tmp_path / "shared"
    shared.mkdir()
    monkeypatch.setenv("SICO_CIRCUIT_TEMPLATES_DIR", str(shared))
    client, _, result = extract(tmp_path)
    private = tmp_path / ".sico/ai/circuit_templates"
    assert private in (Path(result["package"]) / "package.json").parents
    assert not list(shared.iterdir())
    (Path(result["package"]) / "template/topology.json").write_text("{}")
    with pytest.raises(TemplateError, match="digest mismatch"):
        call_template("extract_circuit_templates", request(), workspace=tmp_path, client=client)


def test_failed_missing_schematic_keeps_available_symbol_reference(tmp_path):
    rows = capture_rows()
    rows[0]["assets"] = ["schematic", "symbol"]
    rows[1].update(asset="symbol", view="symbol")
    rows[1:1] = [dict(kind="missing_asset", asset="schematic", view="schematic", reason="missing")]
    rows[-1]["asset_count"] = 2
    args = request()
    args.update(assets=["schematic", "symbol"], classifications={})
    _, _, result = extract(tmp_path, rows=rows, args=args)
    assert not result["ok"] and result["reuse"]["template_ref"] is None
    assert result["reuse"]["issues"][0]["code"] == "missing_schematic"


def test_whole_capture_uses_schematic_as_primary_asset_without_mutating_input():
    args = request() | dict(assets=["symbol", "schematic", "layout"])
    before = copy.deepcopy(args)
    checked = arguments("extract_circuit_templates", args)
    assert checked["assets"] == ["schematic", "symbol", "layout"]
    code = capture_skill(checked, "/private/capture.jsonl")
    assert 'list("schematic" "symbol" "layout") "cad.template.capture.v2"' in code
    assert args == before
