"""Distinct electrical terminals on a shared physical contact remain distinct."""

import copy

import pytest
from test_template_route_deform import bus_case, deformed
from test_template_routing import _inputs, _record
from test_template_wire_attachment import anchor, segment

from cadai.circuit_geometry_schema import MATRICES
from cadai.circuit_spec_schema import digest
from cadai.template_catalog import TemplateCatalog
from cadai.template_route_paths import path_gaps, repair_branches
from cadai.template_route_ports import device_reference
from cadai.template_routing import route_template
from cadai.template_schema import TemplateError
from cadai.template_terminal_contacts import mapping_gaps, shared_contacts
from cadai.template_wire_attachment import compile_reference
from cadai.template_wire_contacts import NET_SEMANTICS, VERSION, capture_contact_evidence
from cadai.template_wire_graph import connect_net


def attest(record):
    asset = record["assets"]["schematic"]
    asset["wire_reference"].update(schema=VERSION, dependency_digest=digest("observed"),
        **capture_contact_evidence({"wire_net_semantics": NET_SEMANTICS}, asset["shapes"]))


def shared_bus(orientation="R0"):
    record, inputs = bus_case(orientation=orientation)
    for geo in record["assets"]["schematic"]["wire_reference"]["geometry"]:
        geo["terminals"][1]["anchors"][0]["xy"] = [0, 0]
    attest(record)
    plan, geometry, _, members, _, pins = inputs
    plan["template_evidence"]["wire_reference_digest"] = digest(
        record["assets"]["schematic"]["wire_reference"])
    for geo in geometry.values():
        geo["terminals"][1]["anchors"][0]["xy"] = [0, 0]
    for i in range(0, len(members), 2):
        members[i+1]["points"] = copy.deepcopy(members[i]["points"])
        pins[i+1]["xy"] = list(pins[i]["xy"])
    return record, inputs


def shared_line(orientation="R0"):
    record = _record()
    record["topology"]["devices"][0]["pins"].append(dict(name="TAP", net="n0"))
    record["assets"]["schematic"]["wire_reference"]["geometry"][0]["terminals"].append(
        dict(name="TAP", anchors=[dict(id="a0", xy=[0, 0])]))
    attest(record)
    inputs = _inputs(record, orientation)
    plan, _, _, members, _, pins = inputs
    plan["template_use"]["devices"][0]["terminal_map"]["TAP"] = "TAP"
    member = copy.deepcopy(members[0])
    member["endpoint"]["terminal"] = "TAP"
    members.append(member)
    pins.append(dict(endpoint=member["endpoint"], xy=member["points"][0], net="N"))
    return record, inputs


@pytest.mark.parametrize("orientation", list(MATRICES))
def test_rigid_reuse_keeps_three_identities_but_one_wire(monkeypatch, orientation):
    record, inputs = shared_line(orientation)
    monkeypatch.setattr(TemplateCatalog, "get", lambda self, ref: record)
    wires, report = route_template(*inputs)
    assert report["nets"][0]["status"] == "reused", report
    assert report["nets"][0]["terminals"] == 3
    assert len(wires) == 1
    assert len(compile_reference(record)["nets"]["n0"]["anchors"]) == 3


@pytest.mark.parametrize("orientation", list(MATRICES))
def test_shared_contacts_deform_without_losing_terminal_coverage(monkeypatch, orientation):
    record, inputs = shared_bus(orientation)
    monkeypatch.setattr(TemplateCatalog, "get", lambda self, ref: record)
    source = compile_reference(record)["nets"]["n0"]
    assert source["gaps"] == []
    assert len(source["anchors"]) == 8 and len(source["terminal_contacts"]) == 4
    assert all(len(c["endpoints"]) == 2 for c in source["terminal_contacts"])
    paths = deformed(record, inputs)
    assert paths
    wires, report = route_template(*inputs)
    assert report["nets"][0]["method"] == "orthogonal_deformation", report
    assert report["metrics"]["routed_terminals"] == 8
    assert len(wires) == len(paths)


def test_terminal_renaming_and_input_order_do_not_change_contact_geometry():
    record, inputs = shared_bus()
    expected = deformed(record, inputs)
    plan, geometry, _, members, _, pins = inputs
    for device in record["topology"]["devices"]:
        for pin in device["pins"]:
            pin["name"] += "_renamed"
        device["pins"].reverse()
    for geo in record["assets"]["schematic"]["wire_reference"]["geometry"]:
        for terminal in geo["terminals"]:
            terminal["name"] += "_renamed"
        geo["terminals"].reverse()
    for device in plan["template_use"]["devices"]:
        device["terminal_map"] = {k+"_renamed": v+"_renamed"
                                  for k, v in device["terminal_map"].items()}
    for geo in geometry.values():
        for terminal in geo["terminals"]:
            terminal["name"] += "_renamed"
    for m in members:
        m["endpoint"]["terminal"] += "_renamed"
    # pin endpoints share member dictionaries in this fixture; only reverse rows.
    members.reverse()
    pins.reverse()
    actual = deformed(record, inputs)
    assert actual
    assert [w["points"] for w in actual] == [w["points"] for w in expected]


@pytest.mark.parametrize("fault,reason", [
    ("split", "source_shared_terminal_contact_separated"),
    ("merge", "target_terminal_contacts_collapsed"),
    ("missing", "source_target_endpoint_coverage_differs"),
    ("duplicate", "ambiguous_coincident_source_terminals"),
])
def test_target_partition_change_never_reaches_repair(monkeypatch, fault, reason):
    record, inputs = shared_line()
    plan, _, _, members, _, _ = inputs
    if fault == "split":
        members[-1]["points"] = [[11, 5], [12, 5]]
    elif fault == "merge":
        # Change the source shared pair into distinct contacts on the same device.
        record["assets"]["schematic"]["wire_reference"]["geometry"][0]["terminals"][1][
            "anchors"][0]["xy"] = [1, 0]
    elif fault == "missing":
        members.pop()
    else:
        members.append(copy.deepcopy(members[-1]))
    plan["template_evidence"]["wire_reference_digest"] = digest(
        record["assets"]["schematic"]["wire_reference"])
    monkeypatch.setattr(TemplateCatalog, "get", lambda self, ref: record)
    _, report = route_template(*inputs)
    assert report["nets"][0]["status"] == "stub"
    assert reason in report["nets"][0]["reasons"]


@pytest.mark.parametrize("bad", [
    {"device": "other", "terminal": "S"}, {"port": "P"}, {}, None,
    {"device": "M0", "terminal": "D"}, {"instance": "M0", "terminal": "S"},
])
def test_duplicate_or_unproven_contacts_remain_ambiguous(bad):
    anchors = [anchor([0, 0]), {"xy": [0, 0], "endpoint": bad}, anchor([10, 0], "M1")]
    assert shared_contacts(anchors) is None
    result = connect_net([segment([0, 0], [10, 0])], anchors, shared_terminals=True)
    assert "ambiguous_coincident_source_terminals" in result["gaps"]


def test_legacy_graph_and_reference_keep_old_coincidence_policy():
    record, _ = shared_line()
    ref = record["assets"]["schematic"]["wire_reference"]
    ref["schema"] = "cad.template.wire-reference.v1"
    gaps = compile_reference(record)["nets"]["n0"]["gaps"]
    assert "ambiguous_coincident_source_terminals" in gaps


def test_same_location_on_different_nets_is_a_source_conflict():
    record, _ = shared_line()
    record["topology"]["nets"].append(dict(id="n1", source_name="OTHER"))
    record["topology"]["devices"][0]["pins"][1]["net"] = "n1"
    rows = compile_reference(record)["nets"]
    assert all("different_source_nets_touch" in r["gaps"] for r in rows.values())


@pytest.mark.parametrize("where", ["topology", "geometry"])
def test_duplicate_named_terminal_inventory_is_rejected(where):
    record, _ = shared_line()
    rows = (record["topology"]["devices"][0]["pins"] if where == "topology" else
            record["assets"]["schematic"]["wire_reference"]["geometry"][0]["terminals"])
    rows.append(copy.deepcopy(rows[0]))
    with pytest.raises(TemplateError, match="terminal identity"):
        compile_reference(record)


def test_shared_contact_does_not_replace_wire_evidence():
    anchors = [anchor([0, 0]), dict(xy=[0, 0], endpoint=dict(device="M0", terminal="S"))]
    result = connect_net([], anchors, shared_terminals=True)
    assert result["terminal_contacts"] and "source_endpoint_not_on_wire" in result["gaps"]


def test_port_pruning_preserves_every_shared_device_terminal():
    record, _ = shared_line()
    asset = record["assets"]["schematic"]
    record["topology"]["ports"] = [dict(name="P", net="n0")]
    asset["wire_reference"]["ports"] = [dict(port="P", id="p0", xy=[-2, 0])]
    asset["shapes"][0]["figure"]["points"][0] = [-2, 0]
    attest(record)
    src = compile_reference(record)["nets"]["n0"]
    pruned = device_reference(src)
    assert pruned["gaps"] == [] and len(pruned["anchors"]) == 3
    assert pruned["terminal_contacts"] == src["terminal_contacts"]
    assert pruned["edges"][0]["points"] == [[0, 0], [4, 0]]


def test_target_checks_keep_off_grid_and_other_net_pin_conflicts():
    record, inputs = shared_line()
    _, _, layout, members, placed, pins = inputs
    paths = [dict(points=[[10, 5], [14, 5]], net="N", owners=["t0", "t1"])]
    pins.append(dict(xy=[10, 5], net="OTHER"))
    assert "template_path_hits_other_net_pin" in path_gaps(paths, members, layout, placed, pins)
    members[-1]["points"][0] = [10.01, 5]
    assert path_gaps(paths, members, layout, placed, []) == ["template_path_off_grid"]


def test_repair_moves_shared_leaf_once_and_tries_its_escape_directions():
    record, inputs = shared_line()
    plan, _, layout, members, _, _ = inputs
    source = compile_reference(record)
    src = source["nets"]["n0"]
    # Keep the frame of the second instance and move the shared contact upward.
    for m in members:
        if m["endpoint"]["instance"] == "t0":
            m["points"] = [[10, 6], [10, 7]]
    members[-1]["points"][-1] = [10, 5]
    layout["instances"]["t0"]["xy"] = [10, 6]
    result = repair_branches(src, source, plan["template_use"], members, layout,
                             {"block": dict(bbox=[[9, 6.5], [11, 8]])}, [], [])
    assert result
    paths, _ = result
    assert len({tuple(p) for w in paths for p in w["points"]}) >= 3
    assert not path_gaps(paths, members, layout, {}, [])
    assert mapping_gaps(src["anchors"], plan["template_use"], members) == []
