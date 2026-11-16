"""Standard Symbol previews enforce use and the capabilities consumed by each style."""

import copy
import json
from pathlib import Path

import pytest
from template_native_content_fixture import guard
from template_qualification_fixture import collect_rows, routed_rows, symbol_rows

from cadai.template_catalog import TemplateCatalog
from cadai.template_schema import TemplateError, TemplateUnavailable
from cadai.template_standard.projection import project
from cadai.template_standard.replay import engine_record
from cadai.template_standard.symbol_checks import validate_receipt
from cadai.template_symbol import call_symbol
from cadai.template_symbol_plan import make_plan


class PreviewClient:
    def __init__(self):
        self.calls = []

    def call(self, method, args):
        self.calls.append((method, args))
        assert method == "eval_skill_native"
        return True, {"ok": True, "value": json.dumps({"ok": True, "stage": "previewed"})}


def arguments(ref, style="rectangular_module"):
    return dict(template_ref=ref, task_ref="task:test", request_id="preview",
                library="SicoTest", cell="StandardSymbol", style=style, port_map={})


def collected(tmp_path, *, symbol=True):
    rows = symbol_rows(schematic=True) if symbol else routed_rows()
    # Admission/interface tests do not exercise source-text fidelity; the
    # unsupported-label refusal has its own fixed captured-symbol regression.
    rows = [
        row
        for row in rows
        if not (row.get("kind") == "shape" and (row.get("figure") or {}).get("objType") == "label")
    ]
    if symbol:
        for row in rows:
            if row.get("kind") == "shape" and row["figure"]["objType"] == "polygon":
                row["figure"]["lpp"] = ["device", "drawing"]
    result, item = collect_rows(tmp_path, rows, dict(library="demo", cell="INV"))
    return result, item, engine_record(item)


def preview(tmp_path, args, client):
    return call_symbol("preview_template_symbol", args, workspace=tmp_path, client=client)


@pytest.mark.parametrize("style", ["rectangular_module", "template_outline"])
@pytest.mark.parametrize("permission", ["deny", "unknown", None])
def test_reference_permission_cannot_be_bypassed_at_preview(tmp_path, monkeypatch, style, permission):
    result, _, record = collected(tmp_path)
    # Hold the selected record at the consumer boundary: no package hash tampering.
    record["standard_use"]["reference"] = permission
    monkeypatch.setattr(TemplateCatalog, "get", lambda self, ref: record)
    client = PreviewClient()
    with pytest.raises(TemplateError, match="allowed reference"):
        preview(tmp_path, arguments(result["template_ref"], style), client)
    assert client.calls == []
    assert not list(tmp_path.rglob("*-symbol-v1"))


@pytest.mark.parametrize("result", ["unknown", "failed", "not_applicable"])
def test_consumed_capability_must_pass_before_plan_or_skill(tmp_path, monkeypatch, result):
    out, _, record = collected(tmp_path)
    capability = "symbol-reference-v1"
    record["standard_capabilities"][capability] = result
    monkeypatch.setattr(TemplateCatalog, "get", lambda self, ref: record)
    client = PreviewClient()
    with pytest.raises(TemplateError, match=capability):
        preview(tmp_path, arguments(out["template_ref"], "template_outline"), client)
    assert client.calls == []
    assert not list(tmp_path.rglob("*-symbol-v1"))


@pytest.mark.parametrize("missing", ["standard_use", "standard_capabilities"])
def test_direct_standard_plan_cannot_drop_admission_metadata(tmp_path, missing):
    out, _, record = collected(tmp_path)
    record.pop(missing)
    with pytest.raises(TemplateError, match="standard symbol"):
        make_plan(record, arguments(out["template_ref"]))


def test_rectangular_plan_uses_schematic_without_requiring_missing_symbol(tmp_path):
    out, _, record = collected(tmp_path, symbol=False)
    assert record["standard_use"]["reuse"] == "unknown"
    # Internal device classification/reuse is not consumed by a Symbol plan.
    assert record["standard_capabilities"]["topology-v1"] == "unknown"
    assert record["standard_capabilities"]["symbol-reference-v1"] == "not_applicable"
    client = PreviewClient()
    result = preview(tmp_path, arguments(out["template_ref"]), client)
    assert result["ok"] and result["plan"]["style"] == "rectangular_module"
    assert len(client.calls) == 1
    assert all(Path(row["path"]).is_file() for row in result["artifacts"])
    with pytest.raises(TemplateError, match="symbol-reference-v1"):
        make_plan(record, arguments(out["template_ref"], "template_outline"))


def test_qualified_outline_is_allowed_without_circuit_reuse_permission(tmp_path):
    out, _, record = collected(tmp_path)
    assert record["standard_use"]["reuse"] == "unknown"
    plan = make_plan(record, arguments(out["template_ref"], "template_outline"))
    assert plan["style"] == "template_outline"
    assert {p["name"] for p in plan["ports"]} == {"A", "Y"}


@pytest.mark.parametrize("style", ["rectangular_module", "template_outline"])
def test_reference_admission_does_not_skip_complete_interface_check(tmp_path, monkeypatch, style):
    out, _, record = collected(tmp_path)
    record["topology"]["ports"][0]["direction"] = "unknown"
    monkeypatch.setattr(TemplateCatalog, "get", lambda self, ref: record)
    client = PreviewClient()
    with pytest.raises(TemplateUnavailable, match="unsupported symbol direction"):
        preview(tmp_path, arguments(out["template_ref"], style), client)
    assert client.calls == []


@pytest.mark.parametrize("level", ["L1", "L2", "L3"])
def test_projected_reference_cannot_enter_symbol_preview(tmp_path, monkeypatch, level):
    out, item = collect_rows(tmp_path, guard(routed_rows()), dict(library="demo", cell="INV"))
    catalog = TemplateCatalog(workspace=tmp_path)
    policy = dict(masters=[{k: d[k] for k in ("target", "fingerprint")}
                          for d in item["package"]["dependencies"].values() if d["kind"] == "master"])
    destination = tmp_path / "public"
    projected = project(catalog, out["template_ref"], level, output=destination, master_policy=policy)
    monkeypatch.setenv("SICO_CIRCUIT_TEMPLATES_DIR", str(destination))
    client = PreviewClient()
    with pytest.raises(TemplateUnavailable, match="offline reference"):
        preview(tmp_path, arguments(projected["template_ref"]), client)
    assert client.calls == []


@pytest.mark.parametrize("method", ["unknown-v1", "", None, 1, []])
def test_unknown_symbol_coordinate_method_is_rejected(tmp_path, method):
    _, item, _ = collected(tmp_path)
    docs = copy.deepcopy(item["documents"])
    docs["symbol"]["frame"]["anchor_method"] = method
    with pytest.raises(TemplateError, match="symbol frame"):
        validate_receipt(docs)
