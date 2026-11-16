from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest

from cadai.mcp import McpServer
from cadai.template_capture import parse_capture
from cadai.template_catalog import TemplateCatalog, build_manifest
from cadai.template_graph import match, normalize_schematic
from cadai.template_preview import symbol_plan
from cadai.template_schema import PIN_POLICY, TemplateError


def legacy_rows(*, rename=False, endpoint="out"):
    m0, m1 = ("Q0", "Q1") if rename else ("M0", "M1")
    rows = [
        {
            "kind": "header",
            "schema_version": 1,
            "status": "raw_capture",
            "lib": "demo",
            "cell": "INV",
            "view": "schematic",
            "library_path": "/reference/demo",
            "tool_version": "test",
            "dbu_per_uu": 100,
            "bbox": [[0, 0], [20, 20]],
        },
        {
            "kind": "instance",
            "name": m0,
            "libName": "gpdk045",
            "cellName": "nmos1v",
            "viewName": "symbol",
            "xy": [0, 0] if not rename else [100, 50],
            "orient": "R0",
            "master_available": True,
        },
        {
            "kind": "instance",
            "name": m1,
            "libName": "gpdk045",
            "cellName": "pmos1v",
            "viewName": "symbol",
            "xy": [0, 10] if not rename else [100, 60],
            "orient": "R0",
            "master_available": True,
        },
        {
            "kind": "net",
            "name": "in",
            "numBits": 1,
            "sigType": "signal",
            "is_global": False,
            "instance_terminal_count": 2,
        },
        {
            "kind": "net",
            "name": endpoint,
            "numBits": 1,
            "sigType": "signal",
            "is_global": False,
            "instance_terminal_count": 2,
        },
        {"kind": "instance_terminal", "instance": m0, "name": "G", "net": "in"},
        {"kind": "instance_terminal", "instance": m1, "name": "G", "net": "in"},
        {"kind": "instance_terminal", "instance": m0, "name": "D", "net": endpoint},
        {"kind": "instance_terminal", "instance": m1, "name": "D", "net": endpoint},
        {"kind": "terminal", "name": "A", "direction": "input", "numBits": 1, "net": "in"},
        {"kind": "terminal", "name": "Y", "direction": "output", "numBits": 1, "net": endpoint},
        {"kind": "pin", "terminal": "A", "net": "in", "figure": None},
        {"kind": "pin", "terminal": "Y", "net": endpoint, "figure": None},
    ]
    counts = Counter(r["kind"] for r in rows)
    rows.append(
        {
            "kind": "footer",
            "complete": True,
            "counts": {
                k: counts[k]
                for k in ("instance", "instance_terminal", "net", "terminal", "pin", "shape")
            },
        }
    )
    return rows


def write_capture(tmp_path: Path, rows, name="capture.jsonl") -> Path:
    path = tmp_path / name
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in rows) + "\n"
    )
    return path


def make_catalog(tmp_path: Path, rows=None) -> Path:
    capture = write_capture(tmp_path, rows or legacy_rows())
    manifest = {
        "schema_version": 1,
        "pin_policy": PIN_POLICY,
        "sources": {"demo": {"cds_lib": "/reference/demo/cds.lib"}},
        "captures": [
            {
                "source": "demo",
                "path": capture.name,
                "category": "digital",
                "sha256": hashlib.sha256(capture.read_bytes()).hexdigest(),
            }
        ],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    output = tmp_path / "catalog.sqlite3"
    build_manifest(manifest_path, output)
    return output


def test_capture_validation_rejects_duplicates_dangling_and_bad_bbox(tmp_path):
    rows = legacy_rows()
    rows[5]["net"] = "missing"
    with pytest.raises(TemplateError):
        parse_capture(write_capture(tmp_path, rows))
    rows = legacy_rows()
    rows[1]["bbox"] = [[3, 0], [1, 2]]
    with pytest.raises(TemplateError):
        parse_capture(write_capture(tmp_path, rows, "bbox.jsonl"))
    rows = legacy_rows()
    rows[2]["name"] = rows[1]["name"]
    with pytest.raises(TemplateError):
        parse_capture(write_capture(tmp_path, rows, "duplicate-name.jsonl"))


def test_graph_match_ignores_names_and_translation_but_keeps_terminals():
    pytest.importorskip("networkx")
    left, _ = normalize_schematic(legacy_rows()[0], legacy_rows()[1:-1])
    changed = legacy_rows(rename=True)
    right, _ = normalize_schematic(changed[0], changed[1:-1])
    assert match(left, right)["matched"] is True
    changed = legacy_rows(endpoint="different")
    right, _ = normalize_schematic(changed[0], changed[1:-1])
    # Local net names are intentionally ignored by direct-level matching.
    assert match(left, right)["matched"] is True
    changed = legacy_rows()
    changed[9]["direction"] = "inputOutput"
    right, _ = normalize_schematic(changed[0], changed[1:-1])
    assert match(left, right)["matched"] is False


def test_catalog_query_detail_and_preview(tmp_path):
    output = make_catalog(tmp_path)
    catalog = TemplateCatalog(tmp_path)
    result = catalog.query({"query": "INV inverter", "limit": 10, "offset": 0})
    assert result["total"] == 1
    ref = result["templates"][0]["template_ref"]
    detail = catalog.detail(
        {"template_ref": ref, "section": "topology", "entity": "devices", "limit": 10, "offset": 0}
    )
    assert detail["returned"] == 2
    preview = catalog.detail(
        {"template_ref": ref, "section": "preview", "entity": "all", "limit": 100, "offset": 0}
    )
    assert preview["ok"] and any(a["path"].endswith("index.html") for a in preview["artifacts"])
    assert output.is_file()


def test_symbol_plan_grid_long_labels_and_unknown_direction():
    plan = symbol_plan(
        [
            {"name": "IO_LONG_BUS", "direction": "inputOutput"},
            {"name": "IN", "direction": "input"},
            {"name": "OUT_LONG", "direction": "output"},
            {"name": "X", "direction": "unknown"},
        ],
        "demo",
    )
    assert plan["unknown_direction_ports"] == ["X"]
    assert plan["checks"]["all_ports_placed"] is False
    assert plan["checks"]["unique_anchors"] and plan["checks"]["on_grid"]
    assert plan["pin_policy"] == PIN_POLICY


def test_template_mcp_query_uses_workspace_root(tmp_path, monkeypatch):
    make_catalog(tmp_path)
    monkeypatch.setenv("SICO_CIRCUIT_TEMPLATES_DIR", str(tmp_path))

    class Client:
        def close(self):
            pass

    server = McpServer(Client(), workspace=tmp_path)
    listed = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {t["name"] for t in listed["result"]["tools"]}
    assert {
        "extract_circuit_templates",
        "query_circuit_templates",
        "get_circuit_template",
        "match_circuit_template",
    } <= names
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "query_circuit_templates", "arguments": {"cell": "INV", "limit": 5}},
        }
    )
    assert response["result"]["isError"] is False
    assert json.loads(response["result"]["content"][0]["text"])["returned"] == 1


def schematic_of(path):
    capture = parse_capture(path)
    asset = capture["assets"]["schematic"]
    return normalize_schematic(asset["header"], asset["rows"])


def test_v2_coordinates_are_integers_relative_to_one_anchor(tmp_path):
    path = write_capture(tmp_path, legacy_rows(rename=True), "coords.jsonl")
    topology, placement = schematic_of(path)
    assert placement["coordinate_space"] == "source_dbu_integer_relative"
    assert placement["dbu_per_uu"] == 100
    # Anchor is the component-wise minimum of the captured instance positions.
    assert placement["anchor"] == {"kind": "instance_min", "source_xy_dbu": [10000, 5000]}
    assert [p["relative_xy"] for p in placement["instances"]] == [[0, 0], [0, 1000]]
    assert all(type(n) is int for p in placement["instances"] for n in p["relative_xy"])
    assert all("xy" not in p for p in placement["instances"])
    assert placement["bbox"] == [[-10000, -5000], [-8000, -3000]]
    assert placement["rounding"]["scaled_values"] >= 8
    assert placement["rounding"]["max_residual_dbu"] == 0
    assert topology["counts"]["devices"] == 2


def test_v2_rounding_is_reported_and_never_silent(tmp_path):
    rows = legacy_rows()
    rows[1]["xy"] = [0.005, 0]
    path = write_capture(tmp_path, rows, "rounded.jsonl")
    _, placement = schematic_of(path)
    assert placement["rounding"]["rounded_values"] > 0
    assert placement["rounding"]["max_residual_dbu"] > 0
    assert any(g["code"] == "coordinate_rounded_to_dbu" for g in placement["gaps"])
    assert all(type(n) is int for p in placement["instances"] for n in (p["relative_xy"] or []))


def test_integer_placement_does_not_change_topology_fingerprint(tmp_path):
    base = write_capture(tmp_path, legacy_rows(), "base.jsonl")
    moved_rows = legacy_rows()
    for row in moved_rows:
        if row["kind"] == "instance":
            row["xy"] = [n + 500 for n in row["xy"]]
    moved = write_capture(tmp_path, moved_rows, "moved.jsonl")
    base_topology, base_placement = schematic_of(base)
    moved_topology, moved_placement = schematic_of(moved)
    assert base_topology["fingerprint"] == moved_topology["fingerprint"]
    # The anchor moves with the instances, so relative positions stay identical.
    assert base_placement["instances"] == moved_placement["instances"]


def test_published_record_stores_integer_relative_placement(tmp_path):
    make_catalog(tmp_path)
    catalog = TemplateCatalog(tmp_path)
    reference = catalog.query({"offset": 0, "limit": 1})["templates"][0]["template_ref"]
    record = catalog.get(reference)
    assert record["schema_version"] == "cad.circuit.template.v2"
    assert record["coordinate_space"] == "source_dbu_integer_relative"
    schematic = record["assets"]["schematic"]
    assert schematic["coordinate_space"] == "source_dbu_integer_relative"
    assert all("xy" not in p for p in schematic["instances"])
    assert all(type(n) is int for p in schematic["instances"] for n in p["relative_xy"])
    assert schematic["anchor"]["source_xy_dbu"] == [0, 0]


def test_legacy_v1_catalog_and_placement_reference_stay_readable(tmp_path):
    import sqlite3

    from cadai.template_circuit import placement_reference
    from cadai.template_schema import SCHEMA_V1

    reference = "tpl_" + "1" * 64
    summary = {
        "template_ref": reference,
        "library": "demo",
        "cell": "INV",
        "category": "reference",
        "assets": ["schematic"],
        "counts": {"devices": 2},
    }
    record = {
        "schema_version": SCHEMA_V1,
        "rule_version": "20260910.4",
        "template_ref": reference,
        "source": {"lib": "demo", "cell": "INV", "view": "schematic"},
        "provenance": {},
        "category": "reference",
        "missing_assets": [],
        "pin_policy": PIN_POLICY,
        "summary": summary,
        "assets": {
            "schematic": {
                "origin": [0, 0],
                "dbu_per_uu": 1000,
                "instances": [
                    {
                        "device": "d0",
                        "source_name": "M0",
                        "xy": [0, 0],
                        "relative_xy": [0, 0],
                        "orient": "R0",
                        "bbox": [[-0.5, -0.5], [0.5, 0.5]],
                    },
                    {
                        "device": "d1",
                        "source_name": "M1",
                        "xy": [0, 0.5],
                        "relative_xy": [0, 0.5],
                        "orient": "R0",
                        "bbox": [[-0.5, 0], [0.5, 1]],
                    },
                ],
                "shapes": [],
                "pins": [],
                "properties": [],
                "masters": [],
                "gaps": [],
            }
        },
    }
    shards = tmp_path / "catalogs"
    shards.mkdir()
    with sqlite3.connect(shards / "legacy-v1.sqlite3") as db:
        db.executescript(
            """
            CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE templates(ref TEXT PRIMARY KEY, library TEXT, cell TEXT, category TEXT,
              device_count INTEGER, fingerprint TEXT, search_text TEXT,
              summary_json TEXT, data_json TEXT);
            """
        )
        db.execute("INSERT INTO metadata VALUES('schema_version',?)", (SCHEMA_V1,))
        db.execute("INSERT INTO metadata VALUES('rule_version',?)", ("20260910.4",))
        db.execute(
            "INSERT INTO templates VALUES(?,?,?,?,?,?,?,?,?)",
            (
                reference,
                "demo",
                "INV",
                "reference",
                2,
                None,
                "inv",
                json.dumps(summary),
                json.dumps(record),
            ),
        )
    catalog = TemplateCatalog(tmp_path)
    loaded = catalog.get(reference)
    assert loaded["schema_version"] == SCHEMA_V1
    info = placement_reference(
        loaded,
        {
            "devices": [
                {"instance": "d0", "source_device": "d0"},
                {"instance": "d1", "source_device": "d1"},
            ]
        },
    )
    assert info["units"] == "source_user_units"
    assert info["coordinate_space"] == "source_user_units"
    assert info["instances"][1]["relative_xy"] == [0, 0.5]
