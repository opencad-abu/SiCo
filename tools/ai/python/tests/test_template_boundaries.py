"""Topology, publication and layout coverage tests using small counterexamples."""

import hashlib
import json
import sys

import pytest
from test_circuit_templates import legacy_rows, make_catalog, write_capture

from cadai.template_capture import parse_capture
from cadai.template_catalog import TemplateCatalog
from cadai.template_graph import match, normalize_schematic
from cadai.template_layout import normalize_layout
from cadai.template_schema import MAX_RESPONSE_BYTES, TemplateError, TemplateUnavailable, arguments


def top(rows=None):
    rows = rows or legacy_rows()
    return normalize_schematic(rows[0], rows[1:-1])[0]


def test_match_missing_dependency_is_explicit(monkeypatch):
    monkeypatch.setitem(sys.modules, "networkx", None)
    with pytest.raises(TemplateUnavailable, match="networkx"):
        match(top(), top())


@pytest.mark.parametrize(
    "fault", ["pin", "global", "voltage", "direction", "port_name", "connection"]
)
def test_graph_distinguishes_electrical_identity(fault):
    pytest.importorskip("networkx")
    left, right = top(), top()
    if fault == "pin":
        right["devices"][0]["pins"][0]["name"] = "B"
    elif fault == "global":
        right["nets"][0]["is_global"] = True
    elif fault == "voltage":
        right["devices"][0]["attributes"]["voltage_class"] = "2v"
    elif fault == "direction":
        right["ports"][0]["direction"] = "output"
    elif fault == "port_name":
        right["ports"][0]["name"] = "RENAMED"
    elif fault == "connection":
        right["devices"][0]["pins"][0]["net"] = right["nets"][0]["id"]
    assert match(left, right)["status"] == "different"


def test_explicit_port_map_and_search_budget():
    pytest.importorskip("networkx")
    left, right = top(), top()
    right["ports"][0]["name"] = "RENAMED"
    result = match(left, right, {"A": "RENAMED"})
    assert result["matched"] and result["mapping"]["ports"]["A"] == "RENAMED"
    assert result["electrical_equivalence_qualified"] is False
    with pytest.raises(TemplateError):
        match(left, right, {"A": "Y"})
    assert match(left, left, max_states=1)["status"] == "inconclusive"


@pytest.mark.parametrize(
    "fault", ["bool_count", "duplicate_json", "xy", "duplicate_property", "endpoint"]
)
def test_malformed_capture_rejected(tmp_path, fault):
    rows = legacy_rows()
    if fault == "bool_count":
        rows[-1]["counts"]["shape"] = False
    elif fault == "xy":
        rows[1]["xy"] = [3]
    elif fault == "duplicate_property":
        p = {
            "kind": "instance_property",
            "instance": "M0",
            "name": "nf",
            "type": "string",
            "value_skill": '"2"',
        }
        rows[1:1] = [p, p.copy()]
    elif fault == "endpoint":
        rows[5]["name"] = rows[7]["name"]
    raw = write_capture(tmp_path, rows).read_bytes()
    if fault == "duplicate_json":
        raw = raw.replace(b'"lib":"demo"', b'"lib":"demo","lib":"other"')
    with pytest.raises(TemplateError):
        parse_capture(raw)


def artifact(path):
    raw = path.read_bytes()
    return {"path": str(path), "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def test_import_is_immutable_and_survives_spool_cleanup(tmp_path):
    spool = tmp_path / "spool"
    spool.mkdir()
    source = write_capture(spool, legacy_rows())
    catalog = TemplateCatalog(tmp_path / "catalog")
    first = catalog.import_capture(artifact(source), spool)
    assert catalog.import_capture(artifact(source), spool) == first
    source.unlink()
    assert catalog.get(first["template_ref"])["capture_sha256"] == first["capture_sha256"]
    from pathlib import Path

    Path(first["capture"]).write_text("corrupt")
    source = write_capture(spool, legacy_rows())
    with pytest.raises(TemplateError, match="conflict"):
        catalog.import_capture(artifact(source), spool)


def test_pagination_covers_all_entities_and_byte_budget(tmp_path):
    rows = legacy_rows()
    rows[1:1] = [
        {
            "kind": "instance_property",
            "instance": "M0",
            "name": "param" + str(n),
            "type": "string",
            "value_skill": "x" * 2000,
        }
        for n in range(70)
    ]
    make_catalog(tmp_path, rows)
    catalog = TemplateCatalog(tmp_path)
    ref = catalog.query({"offset": 0, "limit": 5})["templates"][0]["template_ref"]
    # Properties are paged as independent records, not returned in huge metadata.
    args = arguments(
        "get_circuit_template",
        {"template_ref": ref, "section": "schematic", "entity": "properties", "limit": 200},
    )
    result = catalog.detail(args)
    assert len(json.dumps(result).encode()) < MAX_RESPONSE_BYTES
    assert result["total"] == 70 and 0 < result["returned"] < 70
    collected = list(result["records"])
    while result["next_offset"] is not None:
        args["offset"] = result["next_offset"]
        result = catalog.detail(args)
        collected.extend(result["records"])
    assert len(collected) == len({row["data"]["name"] for row in collected}) == 70


def object_id(name, view):
    return {"object_type": "inst", "name": name, "library": "demo", "cell": "INV", "view": view}


def layout(bound=True):
    return {
        "source": {"lib": "demo", "cell": "INV", "view": "layout"},
        "contexts": [
            {"binding_status": "query_available_unverified" if bound else "binding_unavailable"}
        ],
        "instances": [
            {"name": n, "xy": [x, 0], "bbox": [[x, 0], [x + 1, 1]], "orient": "R0"}
            for n, x in [("F0", 0), ("F1", 2)]
        ],
        "instance_properties": [{"instance": "F0", "name": "nf", "value_skill": '"4"'}],
        "bindings": [
            {
                "instance": n,
                "status": "observed" if bound else "binding_unavailable",
                "objects": [[object_id("M0", "schematic")]] if bound else None,
                "siblings": [[object_id(other, "layout")]] if bound else None,
            }
            for n, other in [("F0", "F1"), ("F1", "F0")]
        ],
        "coverage": {},
    }


def test_fold_and_many_to_many_groups_preserve_members():
    src = {"lib": "demo", "cell": "INV", "view": "schematic"}
    value = normalize_layout(layout(), top(), src)
    group = value["binding_groups"][0]
    assert len(group["logical_members"]) == 1 and len(group["physical_members"]) == 2
    assert group["bbox"] == [[0, 0], [3, 1]]
    assert value["coverage"]["logical_devices_mapped"] == 1
    assert value["unmapped_devices"][0]["reason"] == "missing_unknown"
    assert value["multiplicity_properties"][0]["value_skill"] == '"4"'
    multiple = layout()
    for b in multiple["bindings"]:
        b["objects"].append([object_id("M1", "schematic")])
    result = normalize_layout(multiple, top(), src)
    assert len(result["binding_groups"]) == 1 and result["coverage"]["logical_devices_mapped"] == 2


def test_missing_binder_conflict_and_vector_members_never_imply_flatten():
    src = {"lib": "demo", "cell": "INV", "view": "schematic"}
    no_binder = normalize_layout(layout(False), top(), src)
    assert no_binder["coverage"]["binding_status"] == "binding_unavailable"
    assert no_binder["coverage"]["missing_flattened"] is None
    assert no_binder["coverage"]["logical_devices_mapped"] is None
    conflict = layout()
    conflict["contexts"][0]["binding_status"] = "source_conflict"
    assert not normalize_layout(conflict, top(), src)["binding_groups"]
    vector = layout()
    for b in vector["bindings"]:
        b["objects"][0].extend([3, None, None])
    result = normalize_layout(vector, top(), src)
    assert result["binding_groups"][0]["logical_members"][0]["member"] == 3
    assert result["coverage"]["logical_devices_mapped"] == 0
