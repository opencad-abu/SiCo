"""Preserved symbol labels stay literal, positioned and non-evaluated."""

import copy
import json
from pathlib import Path

import pytest
from test_draw_symbol import args, gate_record

from cadai.template_build import publish
from cadai.template_preview import render_preview
from cadai.template_schema import TemplateUnavailable
from cadai.template_symbol import annotation_rows, call_symbol
from cadai.template_symbol_family import build_family, family_plan
from cadai.template_symbol_labels import label_overlaps
from cadai.template_symbol_plan import make_plan, write_preview


def labelled_record():
    record = gate_record("INVX1", labels=True)
    shape = copy.deepcopy(
        next(
            row
            for row in record["assets"]["symbol"]["shapes"]
            if (row.get("figure") or {}).get("text") == "[@info]"
        )
    )
    shape["figure"].update(
        text="cdsParam(1)",
        labelType="ILLabel",
        xy=[60, -30],
        bBox=[[39, -35], [81, -25]],
    )
    record["assets"]["symbol"]["shapes"].append(shape)
    return record


def dynamic_record():
    record = labelled_record()
    shape = copy.deepcopy(record["assets"]["symbol"]["shapes"][-1])
    shape["figure"].update(
        text='if(isCallable(\'AdfWhichView) AdfWhichView() "")',
        xy=[60, -54],
        bBox=[[0, -60], [120, -48]],
    )
    record["assets"]["symbol"]["shapes"].append(shape)
    return record


def test_preserve_keeps_plain_text_and_cds_param_placeholder(tmp_path):
    record = labelled_record()
    plan = make_plan(record, args(label_policy="preserve"))
    assert plan["style"] == "template_outline"
    assert plan["annotation_count"] == 2
    assert plan["annotation_overlaps"] == []
    by_text = {item["text"]: item for item in plan["annotations"]}
    plain = by_text["[@info]"]
    assert plain["kind"] == "plain_text"
    assert plain["xy"] == [0.375, -0.0375]
    assert plain["justify"] == "centerCenter"
    assert plain["font"] == "stick"
    assert plain["height"] == 0.05
    parameter = by_text["cdsParam(1)"]
    assert parameter["kind"] == "cds_param_placeholder"
    assert parameter["label_type"] == "NLPLabel"
    assert parameter["safe_renderer"] == "cds_param_placeholder_not_evaluated"
    assert plan["selection_box"][0][1] <= -0.21875
    assert annotation_rows(plan)[1][0] == "cdsParam(1)"
    artifacts = write_preview(plan, tmp_path)
    svg_path = next(Path(item["path"]) for item in artifacts if item["path"].endswith(".svg"))
    svg = svg_path.read_text()
    assert "[@info]" in svg and "cdsParam(1)" in svg


def test_preserve_refuses_conditional_dynamic_labels():
    record = dynamic_record()
    with pytest.raises(TemplateUnavailable, match="dynamic labels require explicit replacement"):
        make_plan(record, args(label_policy="preserve"))


def test_preserve_accepts_known_unquoted_cds_term_as_regenerated_label():
    plan = make_plan(gate_record("MX2X1", labels=True), args(label_policy="preserve"))
    assert plan["style"] == "template_outline"
    assert not any("cdsTerm(B)" in item for item in plan["dropped_labels"])


def test_label_overlap_reports_only_material_intersections():
    first = {"text": "A", "bbox": [[0, 0], [1, 1]]}
    edge = {"text": "B", "bbox": [[1, 0], [2, 1]]}
    material = {"text": "C", "bbox": [[0.5, 0], [1.5, 1]]}
    assert label_overlaps([first, edge]) == []
    assert label_overlaps([first, material])[0]["overlap_ratio"] == 0.5


def test_family_records_preserved_annotations():
    family = build_family(labelled_record(), "inv")
    assert [item["text"] for item in family["annotations"]] == ["[@info]", "cdsParam(1)"]
    assert family["annotation_overlaps"] == []
    assert family["dropped_labels"] == []


def test_family_refuses_pre_dropped_dynamic_labels_unless_explicitly_dropped():
    family = build_family(dynamic_record(), "inv")
    assert family["dropped_labels"]
    with pytest.raises(TemplateUnavailable, match="labels are not rendered"):
        family_plan(dynamic_record(), family, args())
    plan = family_plan(dynamic_record(), family, args(label_policy="drop"))
    assert plan["annotations"] == [] and plan["dropped_labels"]


def test_render_preview_keeps_labels_and_reports_dynamic_fallback(tmp_path):
    result = render_preview(labelled_record(), tmp_path / "preserved")
    report = result["reports"]["symbol_plan"]
    assert report["label_policy"] == "preserve"
    assert report["annotation_count"] == 2
    proposed = Path(
        next(
            item["path"]
            for item in result["artifacts"]
            if item["path"].endswith("symbol-proposed.svg")
        )
    ).read_text()
    assert "[@info]" in proposed and "cdsParam(1)" in proposed
    dynamic = render_preview(dynamic_record(), tmp_path / "dynamic")
    report = dynamic["reports"]["symbol_plan"]
    assert report["label_policy"] == "drop"
    assert report["label_fallback"]["code"] == "preserve_outline_unavailable"


def test_preview_passes_preserved_annotations_to_native_payload(tmp_path, monkeypatch):
    record = labelled_record()
    record["template_ref"] = "tpl_" + "c" * 64
    record["summary"]["template_ref"] = record["template_ref"]
    publish([record], tmp_path / "catalog.sqlite3")
    monkeypatch.setenv("SICO_CIRCUIT_TEMPLATES_DIR", str(tmp_path))

    class Client:
        seen = []

        def call(self, method, values):
            self.seen.append((method, values))
            return True, {"ok": True, "value": json.dumps({"ok": True})}

    client = Client()
    result = call_symbol(
        "preview_template_symbol",
        {
            "template_ref": record["template_ref"],
            "task_ref": "task:labels",
            "request_id": "labels",
            "library": "SicoTest",
            "cell": "INV_labels",
            "style": "template_outline",
            "label_policy": "preserve",
            "port_map": {},
        },
        workspace=tmp_path,
        client=client,
    )
    assert result["plan"]["annotation_count"] == 2
    assert "cdsParam(1)" in client.seen[0][1]["code"]
    assert "[@info]" in client.seen[0][1]["code"]
