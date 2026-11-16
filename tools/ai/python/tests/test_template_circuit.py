"""Whole-template endpoint preservation and explicit target parameter boundaries."""

import copy
import json

import pytest
from template_circuit_fixture import persisted_case, selections

from cadai.circuit_generic import checked_preview
from cadai.circuit_spec_schema import CircuitSpecError, digest
from cadai.template_circuit import prepare_template
from cadai.template_schema import TemplateUnavailable


def test_persisted_source_to_explicit_spec_then_shared_preview(tmp_path, monkeypatch):
    args, record = persisted_case(tmp_path, monkeypatch)
    before = copy.deepcopy(args)
    output = prepare_template(args, workspace=tmp_path)
    assert args == before
    plan, spec, use = output["plan"], output["spec"], output["template_use"]
    assert plan["structural_valid"] and plan["binding_complete"]
    assert spec["instances"][0]["connections"] == {"G": "A", "D": "Y", "S": "gnd!", "B": "gnd!"}
    assert spec["instances"][1]["connections"] == {"G": "A", "D": "Y", "S": "vdd!", "B": "vdd!"}
    assert [i["parameters"] for i in spec["instances"]] == [
        d["parameters"] for d in args["devices"]
    ]
    assert "SOURCE_WIDTH" not in json.dumps(output) and "SOURCE_FINGERS" not in json.dumps(output)
    assert "SOURCE_WIDTH" in json.dumps(record["assets"]["schematic"]["properties"])
    assert all(
        not plan[k]
        for k in (
            "creation_ready",
            "live_binding_verified",
            "binding_source_verified",
            "simulation_qualified",
        )
    )
    evidence = plan["template_evidence"]
    assert evidence["mapping_verified"]
    assert (
        not evidence["device_equivalence_verified"]
        and not evidence["multiplicity_mapping_verified"]
    )
    assert evidence["source_gaps"][0]["code"] == "inherited_connectivity_not_expanded"
    assert len(evidence["omitted_endpoint_free_nets"]) == 1
    assert evidence["device_references"][0]["source_property_names"] == ["w", "nf"]
    assert evidence["source_properties_ref"]["template_ref"] == record["template_ref"]
    placement = plan["placement"]["template_reference"]
    # Schema v2: integer source DBU offsets relative to the template anchor.
    assert [p["relative_xy"] for p in placement["instances"]] == [[0, 0], [0, 1000]]
    assert placement["units"] == "source_dbu_integer"
    assert not placement["gaps"] and not placement["target_coordinates_verified"]
    assert not placement["source_wires_reused"]
    assert {p["name"]: p["region"] for p in plan["placement"]["ports"]} == {
        "gnd!": "upper_left",
        "vdd!": "upper_left",
        "A": "lower_left",
        "Y": "right",
    }
    replay = checked_preview(
        {"spec": spec, "bindings": args["bindings"], "template_use": use}, workspace=tmp_path
    )
    assert replay["plan"] == plan and replay["preview_digest"] == output[
        "preview_digest"
    ] == digest(plan)
    args["devices"].reverse()
    assert prepare_template(args, workspace=tmp_path)["preview_digest"] == output["preview_digest"]


def test_two_explicit_project_interfaces_no_pdk_defaults(tmp_path, monkeypatch):
    args, record = persisted_case(tmp_path, monkeypatch)
    first = prepare_template(args, workspace=tmp_path)
    second = prepare_template(selections(record, "b"), workspace=tmp_path)
    assert second["plan"]["binding_complete"]
    assert second["spec"]["instances"][0]["connections"] == {
        "pin_g": "A",
        "pin_d": "Y",
        "pin_s": "gnd!",
        "pin_b": "gnd!",
    }
    assert second["spec"]["instances"][0]["parameters"] == {
        "w_eff": "TARGET_WIDTH_b",
        "fold_count": 2,
    }
    assert first["preview_digest"] != second["preview_digest"]
    with pytest.raises(CircuitSpecError, match="project_ref/binding_snapshot"):
        checked_preview(
            {
                "spec": first["spec"],
                "bindings": second["plan"]["bindings"],
                "template_use": first["template_use"],
            },
            workspace=tmp_path,
        )


@pytest.mark.parametrize(
    "case",
    [
        "endpoint",
        "direction",
        "extra_instance",
        "extra_net",
        "master",
        "partial_net_map",
        "partial_terminal_map",
    ],
)
def test_reused_template_mapping_rejects_changed_topology(tmp_path, monkeypatch, case):
    args, _ = persisted_case(tmp_path, monkeypatch)
    prepared = prepare_template(args, workspace=tmp_path)
    request = {
        "spec": prepared["spec"],
        "bindings": args["bindings"],
        "template_use": prepared["template_use"],
    }
    spec, use = request["spec"], request["template_use"]
    if case == "endpoint":
        spec["instances"][0]["connections"]["G"] = "Y"
    elif case == "direction":
        spec["ports"][0]["direction"] = "output"
    elif case == "extra_instance":
        extra = copy.deepcopy(spec["instances"][0])
        extra.update(id="extra", name="Mextra")
        spec["instances"].append(extra)
    elif case == "extra_net":
        spec["nets"].append({"name": "extra", "scope": "local"})
        spec["ports"].append({"name": "extra", "net": "extra", "direction": "input"})
    elif case == "master":
        spec["instances"][0]["master"] = "chosen_1"
    elif case == "partial_net_map":
        use["net_map"].pop(next(iter(use["net_map"])))
    else:
        use["devices"][0]["terminal_map"].pop("G")
    with pytest.raises(CircuitSpecError):
        checked_preview(request, workspace=tmp_path)


def test_target_sizing_is_editable_and_changes_digest(tmp_path, monkeypatch):
    args, _ = persisted_case(tmp_path, monkeypatch)
    prepared = prepare_template(args, workspace=tmp_path)
    spec = prepared["spec"]
    expression = 'TARGET_WIDTH * 2 + "opaque text"'
    spec["instances"][0]["parameters"]["width"] = expression
    replay = checked_preview(
        {"spec": spec, "bindings": args["bindings"], "template_use": prepared["template_use"]},
        workspace=tmp_path,
    )
    assert replay["preview_digest"] != prepared["preview_digest"]
    assert replay["plan"]["spec"]["instances"][0]["parameters"]["width"] == expression
    assert replay["plan"]["template_evidence"]["mapping_verified"]


@pytest.mark.parametrize(
    "case,code",
    [
        ("missing", "master_binding_missing"),
        ("incomplete", "terminal_metadata_incomplete"),
        ("callbacks", "callback_extension_not_validated"),
        ("extra_pin", "terminal_unassigned"),
    ],
)
def test_incomplete_binding_remains_a_gap(tmp_path, monkeypatch, case, code):
    args, _ = persisted_case(tmp_path, monkeypatch)
    master = args["bindings"]["masters"][0]
    if case == "missing":
        args["bindings"]["masters"].pop(0)
    elif case == "incomplete":
        master["terminals_complete"] = False
    elif case == "callbacks":
        master["callbacks"] = {"status": "required", "extension_ref": "project:explicit-extension"}
    else:
        master["terminals"].append({"name": "NC", "direction": "inputOutput"})
    output = prepare_template(args, workspace=tmp_path)
    assert output["status"] == "binding_incomplete"
    assert code in {i["code"] for i in output["plan"]["issues"]}
    assert not output["plan"]["creation_ready"]


def test_missing_source_placement_does_not_discard_topology(tmp_path, monkeypatch):
    def change(rows):
        inst = next(r for r in rows if r["kind"] == "instance")
        inst.update(xy=None, bbox=None, orient=None)

    args, _ = persisted_case(tmp_path, monkeypatch, change)
    plan = prepare_template(args, workspace=tmp_path)["plan"]
    assert plan["binding_complete"]
    reference = plan["placement"]["template_reference"]
    assert reference["gaps"] == [{"instance": "target_0", "code": "source_placement_incomplete"}]
    assert reference["instances"][0]["relative_xy"] is None


def test_source_open_pin_requires_explicit_reason(tmp_path, monkeypatch):
    def change(rows):
        inst = next(r for r in rows if r["kind"] == "instance")
        inst["terminal_names"].append("NC")

    args, _ = persisted_case(tmp_path, monkeypatch, change)
    with pytest.raises(CircuitSpecError, match="unconnected reasons"):
        prepare_template(args, workspace=tmp_path)
    args["devices"][0]["unconnected"] = {"NC": "source diagnostic pin intentionally open"}
    plan = prepare_template(args, workspace=tmp_path)["plan"]
    assert plan["binding_complete"] and plan["warnings"][0]["code"] == "intentional_unconnected"


def test_hierarchy_master_stays_explicit_design(tmp_path, monkeypatch):
    def change(rows):
        next(r for r in rows if r["kind"] == "instance")["schematic_available"] = True
        next(r for r in rows if r["kind"] == "instance")["libName"] = "project_design"

    args, _ = persisted_case(tmp_path, monkeypatch, change)
    with pytest.raises(CircuitSpecError, match="role conflicts"):
        prepare_template(args, workspace=tmp_path)
    args["devices"][0].pop("name")
    args["bindings"]["masters"][0]["kind"] = "design"
    assert (
        prepare_template(args, workspace=tmp_path)["spec"]["instances"][0]["name"] == "I_device_0_0"
    )


@pytest.mark.parametrize("case", ["bus", "array", "pin_bundle", "sig_type"])
def test_unrepresentable_source_semantics_are_rejected(tmp_path, monkeypatch, case):
    def change(rows):
        if case in {"bus", "sig_type"}:
            row = next(r for r in rows if r["kind"] == "net" and r["name"] == "in")
            row["numBits" if case == "bus" else "sigType"] = 2 if case == "bus" else "power"
        elif case == "pin_bundle":
            next(r for r in rows if r["kind"] == "instance")["terminal_names"].append("DATA<3:0>")
        else:
            for r in rows:
                for key in ("instance", "name"):
                    if r.get(key) == "M0":
                        r[key] = "M<3:0>"

    args, _ = persisted_case(tmp_path, monkeypatch, change)
    if case == "pin_bundle":
        args["bindings"]["masters"][0]["terminals"][-1]["name"] = "DATA"
        args["devices"][0]["terminal_map"]["DATA<3:0>"] = "DATA"
    with pytest.raises(TemplateUnavailable):
        prepare_template(args, workspace=tmp_path)
