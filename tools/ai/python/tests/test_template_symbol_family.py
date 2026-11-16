"""Reusable symbol-family geometry must bind terminals without copying a view."""

import copy
import json
from pathlib import Path

import pytest
from test_draw_symbol import gate_record

from cadai.template_build import publish
from cadai.template_locations import template_root
from cadai.template_schema import TemplateError, TemplateUnavailable
from cadai.template_symbol import call_symbol
from cadai.template_symbol_family import (
    _hint_matches,
    build_family,
    dedupe_families,
    family_plan,
    select_family,
)
from cadai.template_symbol_family_layout import drawn_selection_box
from cadai.template_symbol_family_registry import build_registry, read_registry, write_registry
from cadai.template_symbol_plan import make_plan, write_preview


def args(**extra):
    return {
        "library": "familyWork",
        "cell": "FamilyCell",
        "port_map": {},
        "style": "symbol_family",
        **extra,
    }


def test_family_binds_renamed_terminals_and_keeps_geometry(tmp_path):
    record = gate_record("INVX1")
    family = build_family(record, "inv")
    assert [slot["name"] for slot in family["slots"]] == ["A", "VDD", "VSS", "Y"]

    target = copy.deepcopy(record)
    target["topology"]["ports"][0]["name"] = "IN"
    target["topology"]["ports"][3]["name"] = "OUT"
    plan = family_plan(target, family, args(slot_map={"IN": "A", "OUT": "Y"}))
    assert plan["style"] == "symbol_family"
    assert [row[0] for row in plan["rows"]] == ["IN", "VDD", "VSS", "OUT"]
    assert plan["body"] == family["body"] and plan["stubs"] == family["stubs"]
    assert all(a["name"] != "A" and a["name"] != "Y" for a in plan["outline_pins"])
    assert all(Path(a["path"]).is_file() for a in write_preview(plan, tmp_path))


def test_family_solves_aoi21_contact_fallback():
    record = gate_record("AOI21X1")
    family = build_family(record, "aoi21")
    plan = make_plan(record, {**args(), "family": family, "style": "auto"})
    assert plan["style"] == "symbol_family"
    assert plan["family_ref"] == "aoi21"
    assert plan["width"] >= 1 and plan["height"] >= 1
    assert all(row[5] in (0.125, 0.875) for row in plan["bootstrap_rows"])
    assert len(plan["body"]) == 12 and len(plan["stubs"]) == 6
    assert len(plan["rows"]) == 6


def test_family_selection_box_tracks_drawn_geometry():
    family = build_family(gate_record("INVX1"), "inv")
    family["annotations"] = []
    family["annotation_overlaps"] = []
    family["selection_box"] = [[-1.0, -2.0], [1.0, 2.0]]
    plan = family_plan(copy.deepcopy(gate_record("INVX1")), family, args(label_policy="drop"))
    assert plan["selection_box"] != family["selection_box"]
    assert drawn_selection_box(
        [["line", [[0, 0], [1, 1]], None]],
        [["line", [[-0.5, 0.5], [0, 0.5]], None]],
        [{"origin": [-1, 1]}],
        [{"bbox": [[-1.5, -1], [0, 0]]}],
    ) == [[-1.5, -1], [1, 1]]


def test_family_separates_crowded_side_pin_labels():
    record = gate_record("INVX1")
    family = build_family(record, "inv")
    for slot, y in zip(family["slots"][:3], (0, -0.0625, -0.125)):
        slot["side"] = "left"
        slot["anchor"] = [0.125, y]
    family["slots"][3]["side"] = "right"
    family["slots"][3]["anchor"] = [0.625, 0]
    plan = family_plan(copy.deepcopy(record), family, args())
    labels = {row[0]: row[1] for row in plan["pin_labels"]}
    assert len({labels["A"][0], labels["VDD"][0], labels["VSS"][0]}) > 1


def test_family_can_center_pg_slots_without_changing_source_default():
    record = gate_record("INVX1")
    family = build_family(record, "inv", roles={"VDD": "power", "VSS": "ground"})
    for slot in family["slots"]:
        if slot["name"] in {"VDD", "VSS"}:
            slot["side"] = "left"
            slot["anchor"] = [0.125, 0.25]
    source = family_plan(copy.deepcopy(record), family, args())
    source_sides = {
        p["name"]: p["side"] for p in source["outline_pins"] if p["name"] in {"VDD", "VSS"}
    }
    assert source_sides == {
        "VDD": "left",
        "VSS": "left",
    }
    family["pg_layout"] = "center"
    centered = family_plan(copy.deepcopy(record), family, args())
    pins = {p["name"]: p for p in centered["outline_pins"]}
    assert pins["VDD"]["side"] == "top" and pins["VSS"]["side"] == "bottom"
    assert centered["pg_layout"] == "center"


def test_family_requires_every_slot_to_be_bound():
    record = gate_record("INVX1")
    family = build_family(record, "inv")
    target = copy.deepcopy(record)
    target["topology"]["ports"] = target["topology"]["ports"][:2]
    with pytest.raises(TemplateUnavailable, match="slot is not bound"):
        family_plan(target, family, args())


def test_family_dedupe_and_hint_selection():
    record = gate_record("INVX1")
    family = build_family(record, "inv")
    alias = json.loads(json.dumps(family))
    alias["family_id"] = "inv_alias"
    alias["source"]["cell"] = "INVX1_alias"
    deduped = dedupe_families([family, alias])
    assert len(deduped) == 1
    assert deduped[0]["family_id"] == "inv"
    assert len(deduped[0]["aliases"]) == 2
    assert dedupe_families([alias, family]) == deduped
    assert select_family(record, {"inv": family}, {"inv": ["inv"]})[0] == "inv"
    assert (
        select_family(
            record,
            {"nand2": build_family(gate_record("NAND2X1"), "nand2")},
            {"nand2": ["nand2"]},
        )
        is None
    )
    with pytest.raises(TemplateError, match="ambiguous"):
        select_family(record, {"inv": family, "alias": alias}, {"inv": ["inv"], "alias": ["inv"]})
    xor = build_family(gate_record("XOR2X1"), "xor2")
    or2 = build_family(gate_record("OR2X1"), "or2")
    assert (
        select_family(
            gate_record("XOR2X1"),
            {"xor2": xor, "or2": or2},
            {"xor2": ["xor2"], "or2": ["or2"]},
        )[0]
        == "xor2"
    )
    xnor = build_family(gate_record("XNOR2X1"), "xnor2")
    nor = build_family(gate_record("NOR2X1"), "nor2")
    assert (
        select_family(
            gate_record("XNOR2X1"),
            {"xnor2": xnor, "nor2": nor},
            {"xnor2": ["xnor2"], "nor2": ["nor2"]},
        )[0]
        == "xnor2"
    )
    assert _hint_matches("LDO_opamp", "opamp")
    assert not _hint_matches("XOR2X1", "or2")
    assert not _hint_matches("XNOR2X1", "nor2")


def test_call_symbol_auto_selects_private_family_index(tmp_path, monkeypatch):
    record = gate_record("INVX1")
    record["template_ref"] = "tpl_" + "a" * 64
    record["summary"]["template_ref"] = record["template_ref"]
    publish([record], tmp_path / "catalog.sqlite3")
    monkeypatch.setenv("SICO_CIRCUIT_TEMPLATES_DIR", str(tmp_path))
    family_root = template_root(tmp_path, create=True) / "symbol_families"
    family_root.mkdir(parents=True, exist_ok=True)
    family = build_family(record, "inv", roles={"VDD": "power", "VSS": "ground"})
    (family_root / "inv.json").write_text(json.dumps(family))
    (family_root / "index.json").write_text(
        json.dumps(
            {
                "format": "sico.symbol.family.index.v1",
                "families": {"inv": {"file": "inv.json", "cell_hints": ["inv"]}},
            }
        )
    )

    class Client:
        def call(self, method, arguments):
            return True, {"ok": True, "value": json.dumps({"ok": True, "stage": "previewed"})}

    result = call_symbol(
        "preview_template_symbol",
        {
            "template_ref": record["template_ref"],
            "task_ref": "task:family",
            "request_id": "family",
            "library": "SicoTest",
            "cell": "INVX1_family",
            "style": "auto",
            "port_map": {},
        },
        workspace=tmp_path,
        client=Client(),
    )
    assert result["ok"] and result["plan"]["style"] == "symbol_family"
    assert result["plan"]["family_ref"] == "inv"


def test_registry_build_write_read_and_escape_refusal(tmp_path):
    records = {
        ("gsclib045", "INVX1"): gate_record("INVX1"),
        ("gsclib045", "NAND2X1"): gate_record("NAND2X1"),
    }
    specs = [
        {
            "library": "gsclib045",
            "cell": "INVX1",
            "family_id": "inv",
            "cell_hints": ["inv"],
            "roles": {"VDD": "power", "VSS": "ground"},
        },
        {
            "library": "gsclib045",
            "cell": "NAND2X1",
            "family_id": "nand2",
            "cell_hints": ["nand2"],
            "roles": {"VDD": "power", "VSS": "ground"},
        },
    ]
    families, hints = build_registry(records, specs)
    assert {f["family_id"] for f in families} == {"inv", "nand2"}
    root = tmp_path / "families"
    write_registry(root, families, hints)
    loaded, loaded_hints = read_registry(root)
    assert set(loaded) == {"inv", "nand2"}
    assert loaded_hints["inv"] == ["inv"]
    with pytest.raises(TemplateError, match="duplicate canonical"):
        write_registry(
            tmp_path / "duplicate",
            [loaded["inv"], loaded["inv"]],
            loaded_hints,
        )
    (root / "index.json").write_text(
        json.dumps(
            {
                "format": "sico.symbol.family.index.v1",
                "families": {"bad": {"file": "../bad.json"}},
            }
        )
    )
    with pytest.raises(TemplateError, match="invalid"):
        read_registry(root)
