import json

import pytest

from cadai.pdk_errors import PdkUnavailable
from cadai.pdk_standard.layout import validate_device, validate_process


def process():
    return {
        "source": "techfile",
        "depends_on": [],
        "unit": "m",
        "database_unit": 1e-9,
        "dbu_per_um": 1000,
        "user_unit": "micron",
        "grid": {"step": [5e-9, 5e-9], "origin": [0, 0]},
        "layers": {
            "m1_drawing": {
                "name": "M1", "purpose": "drawing",
                "gds_layer": 61, "gds_datatype": 0,
                "kind": "conductor", "usage": ["drawing", "pin"],
                "direction": "vertical", "stack_order": 1,
                "min_width": 5e-8, "min_spacing": 5e-8,
            },
            "m1_pin": {
                "name": "M1", "purpose": "pin",
                "gds_layer": 61, "gds_datatype": 1,
                "kind": "conductor", "usage": ["pin"],
            },
            "m1_text": {
                "name": "M1", "purpose": "label",
                "gds_layer": 61, "gds_datatype": 250,
                "kind": "text", "usage": ["label", "text"],
            },
            "m2_drawing": {
                "name": "M2", "purpose": "drawing",
                "gds_layer": 62, "gds_datatype": 0,
                "kind": "conductor", "usage": ["drawing", "pin"],
                "direction": "horizontal", "stack_order": 2,
            },
            "m2_pin": {
                "name": "M2", "purpose": "pin",
                "gds_layer": 62, "gds_datatype": 1,
                "kind": "conductor", "usage": ["pin"],
            },
            "po_drawing": {
                "name": "PO", "purpose": "drawing",
                "gds_layer": 17, "gds_datatype": 0,
                "kind": "device", "usage": ["drawing"],
            },
            "po_pin": {
                "name": "PO", "purpose": "pin",
                "gds_layer": 17, "gds_datatype": 1,
                "kind": "conductor", "usage": ["pin"],
            },
            "od_drawing": {
                "name": "OD", "purpose": "drawing",
                "gds_layer": 6, "gds_datatype": 0,
                "kind": "device", "usage": ["drawing"],
            },
            "od_pin": {
                "name": "OD", "purpose": "pin",
                "gds_layer": 6, "gds_datatype": 1,
                "kind": "conductor", "usage": ["pin"],
            },
            "via1_drawing": {
                "name": "VIA1", "purpose": "drawing",
                "gds_layer": 51, "gds_datatype": 0,
                "kind": "cut", "usage": ["drawing"],
            },
            "marker_drawing": {
                "name": "PDK", "purpose": "drawing",
                "gds_layer": 100, "gds_datatype": 0,
                "kind": "marker", "usage": ["marker"],
            },
        },
        "layer_groups": {
            "routing": {"members": ["m1_drawing", "m2_drawing"], "usage": ["drawing"], "order": 1},
            "pin": {"members": ["m1_pin", "m2_pin", "po_pin", "od_pin"],
                    "usage": ["pin"], "order": 2},
            "text": {"members": ["m1_text"], "usage": ["label", "text"], "order": 3},
            "marker": {"members": ["marker_drawing"], "usage": ["marker"], "order": 4},
        },
        "vias": {
            "via_m2_m1": {
                "cut_layer": "via1_drawing", "bottom_layer": "m1_drawing",
                "top_layer": "m2_drawing", "cut_size": [2e-8, 2e-8],
                "cut_spacing": 2e-8, "bottom_enclosure": 1e-8,
                "top_enclosure": 1e-8, "max_array": [2, 2],
            }
        },
        "routing": {
            "track_grid": 5e-8,
            "wire_widths": [5e-8, 7e-8],
            "layer_order": ["m1_drawing", "m2_drawing"],
            "spacings": {
                "m1_default": {"layer": "m1_drawing", "width": 5e-8, "spacing": 5e-8},
                "m2_default": {"layer": "m2_drawing", "width": 7e-8, "spacing": 7e-8},
            },
        },
        "stream": {
            "layer_map_ref": "stream_map",
            "layer_map_sha": "sha256:" + "1" * 64,
            "gds_unit": 1e-6,
            "precision": 1e-9,
        },
        "pin_lpp_map": {
            "m1_drawing": "m1_pin",
            "po_drawing": "po_pin",
            "od_drawing": "od_pin",
        },
    }


def device():
    return {
        "source": "layout_capture",
        "depends_on": [],
        "unit": "m",
        "master": {"library": "demo", "cell": "nmos", "view": "layout"},
        "pcells": ["nmos"],
        "pcell_views": {"nmos": ["layout", "symbol", "auLvs"]},
        "variant": "parameterized",
        "layout_available": True,
        "layout_unavailable_reason": None,
        "x_layout_ready": True,
        "parameters": {
            "w": {
                "cdf": "w", "type": "number", "unit": "m", "meaning": "finger width",
                "requirement": "explicit", "layout_relevant": True,
                "resolution": "explicit", "semantic": "finger_width",
            },
            "l": {
                "cdf": "l", "type": "number", "unit": "m", "meaning": "channel length",
                "requirement": "explicit", "layout_relevant": True,
                "resolution": "explicit", "semantic": "channel_length",
            },
            "fingers": {
                "cdf": "fingers", "type": "integer", "unit": None, "meaning": "finger count",
                "requirement": "explicit", "layout_relevant": True,
                "resolution": "explicit", "semantic": "finger_count",
                "domain": {"min": 1, "max": 64},
            },
            "m": {
                "cdf": "m", "type": "integer", "unit": None, "meaning": "multiplier",
                "requirement": "explicit", "layout_relevant": True,
                "resolution": "explicit", "semantic": "multiplier",
                "domain": {"min": 1, "max": 64},
            },
            "model": {
                "cdf": "model", "type": "string", "unit": None, "meaning": "model name",
                "requirement": "explicit", "layout_relevant": True,
                "resolution": "explicit", "semantic": "model_name",
                "domain": {"values": ["nmos"]},
            },
            "total_width": {
                "type": "number", "unit": "m", "meaning": "total finger width",
                "requirement": "optional", "layout_relevant": True,
                "resolution": "derived", "semantic": "total_width",
                "formula": "w * fingers * m", "formula_inputs": ["w", "fingers", "m"],
            },
            "bodytie_type": {
                "cdf": "bodytie_type", "type": "string", "unit": None,
                "meaning": "body tie", "requirement": "explicit", "layout_relevant": True,
                "resolution": "explicit", "semantic": "body_tie",
                "domain": {"values": ["None", "Integred", "Detached"]},
                "layout_effect": {
                    "None": {"tap": "none"},
                    "Integred": {"tap": "merged_with_source_drain"},
                    "Detached": {"tap": "separate", "must_connect": ["B"]},
                },
            },
            "bulkConnectL": {
                "cdf": "bulkConnectL", "type": "boolean", "unit": None,
                "meaning": "left bulk connection", "requirement": "explicit",
                "layout_relevant": True, "resolution": "explicit",
                "semantic": "bulk_connect",
            },
        },
        "terminals": {
            "G": {
                "order": 0, "direction": "input", "pin_source": "oa_pin_attribute",
                "drawing_layers": ["PO/drawing"], "routing_lpp": None,
                "routing_order": None, "requires_contact_to": "M1/drawing",
                "publish": {"lpp": "po_pin", "label": "G", "label_inside_shape": True},
                "must_connect": False, "pin_shape_kind": "rect",
            },
            "D": {
                "order": 1, "direction": "inputOutput",
                "pin_source": "oa_pin_attribute",
                "drawing_layers": ["M1/drawing", "OD/drawing"],
                "routing_lpp": "M1/drawing", "routing_order": 0,
                "requires_contact_to": None,
                "publish": {"lpp": "m1_pin", "label": "D"},
                "must_connect": False, "pin_shape_kind": "rect",
            },
            "B": {
                "order": 2, "direction": "inputOutput",
                "pin_source": "oa_pin_attribute",
                "drawing_layers": ["M1/drawing"],
                "routing_lpp": "M1/drawing", "routing_order": 0,
                "requires_contact_to": None,
                "publish": {"lpp": "m1_pin", "label": "B"},
                "must_connect": True, "pin_shape_kind": "path",
            },
        },
        "constraints": [{
            "id": "mustConnect_0_B", "kind": "must_connect",
            "terminals": ["B"], "net": "B",
            "reason": "the two body taps must be connected",
        }],
        "x_layout_qualification": {
            "drc": "clean; official deck",
            "lvs": "CORRECT; four terminals",
            "parameters": {
                "l": "0.28um", "w_range": "0.1um..10um",
                "fingers_range": "1..2",
                "bodytie_type": ["None", "Integred", "Detached"],
            },
            "scope": ["demo/nmos"],
            "evidence_digest": "sha256:" + "2" * 64,
        },
        "placement_policy": {
            "orientations": ["R0", "MX"],
            "gate_axis": "vertical",
            "pin_access_sides": ["bottom"],
        },
        "guard_ring": {"supported": True, "margin": 2e-6, "layer": "m1_drawing"},
    }


def validate(process_value=None, device_value=None, **kwargs):
    process_value = process() if process_value is None else process_value
    device_value = device() if device_value is None else device_value
    term_order = kwargs.pop("term_order", ["G", "D", "B"])
    validate_process(process_value)
    validate_device(
        device_value,
        process=process_value,
        cdf_parameters=set(device_value["parameters"]),
        term_order=term_order,
        **kwargs,
    )


def test_minimal_base_and_device_layout_validate():
    validate()


def test_optional_profiles_and_envelopes_can_be_absent():
    device_value = device()
    device_value.pop("placement_policy")
    device_value.pop("guard_ring")
    validate(device_value=device_value)


def test_gds_zero_is_valid_but_duplicate_gds_pair_is_rejected():
    process_value = process()
    process_value["layers"]["m1_drawing"]["gds_layer"] = 0
    validate_process(process_value)
    process_value["layers"]["m1_text"].update(gds_layer=0, gds_datatype=0)
    with pytest.raises(PdkUnavailable, match="Duplicate GDS"):
        validate_process(process_value)


def test_layer_usage_and_lpp_references_are_closed():
    process_value = process()
    process_value["layers"]["m1_drawing"]["usage"] = []
    with pytest.raises(PdkUnavailable, match="layer usage"):
        validate_process(process_value)

    device_value = device()
    device_value["terminals"]["G"]["drawing_layers"] = ["MISSING/drawing"]
    with pytest.raises(PdkUnavailable, match="unknown layer"):
        validate(device_value=device_value)


def test_pcell_geometry_is_not_part_of_the_standard_document():
    device_value = device()
    device_value["bbox"] = [[0, 0], [1, 1]]
    with pytest.raises(PdkUnavailable, match="Runtime geometry"):
        validate(device_value=device_value)


def test_dynamic_hierarchy_cannot_carry_children():
    device_value = device()
    device_value["hierarchy"] = {"mode": "dynamic", "children": [{}]}
    with pytest.raises(PdkUnavailable, match="dynamic hierarchy"):
        validate(device_value=device_value)


def test_fixed_hierarchy_is_an_optional_stable_profile():
    device_value = device()
    device_value["gds_structure"] = "nmos_core"
    device_value["hierarchy"] = {
        "mode": "fixed",
        "children": [{
            "name": "M0",
            "master": {"library": "demo", "cell": "nmos_core", "view": "layout"},
            "gds_structure": "nmos_core",
            "usage": "device",
            "origin": [0, 0],
            "orientation": "R0",
            "array": [1, 1],
            "terminal_map": {"G": "G"},
        }],
    }
    validate(device_value=device_value)


def test_cdf_and_terminal_order_cross_checks_fail_closed():
    device_value = device()
    with pytest.raises(PdkUnavailable, match="unknown CDF"):
        validate_device(device_value, process=process(), cdf_parameters={"l"},
                        term_order=["G", "D", "B"])
    with pytest.raises(PdkUnavailable, match="term_order"):
        validate(term_order=["D"])


def test_routing_and_units_are_consistent_with_layer_order():
    process_value = process()
    process_value["routing"]["wire_widths"] = [5e-8]
    with pytest.raises(PdkUnavailable, match="one-to-one"):
        validate_process(process_value)

    process_value = process()
    process_value["dbu_per_um"] = 999
    with pytest.raises(PdkUnavailable, match="dbu_per_um"):
        validate_process(process_value)

    process_value = process()
    process_value["stream"].pop("layer_map_sha")
    with pytest.raises(PdkUnavailable, match="layer_map_sha"):
        validate_process(process_value)


def test_vias_must_cover_the_routing_stack():
    process_value = process()
    process_value["vias"] = {}
    with pytest.raises(PdkUnavailable, match="cover adjacent"):
        validate_process(process_value)


def test_pin_lpp_map_must_reference_pin_layers():
    process_value = process()
    process_value["pin_lpp_map"]["m1_drawing"] = "po_pin"
    with pytest.raises(PdkUnavailable, match="own pin purpose"):
        validate_process(process_value)


def test_availability_requires_the_complete_contract():
    device_value = device()
    device_value.pop("x_layout_qualification")
    with pytest.raises(PdkUnavailable, match="x_layout_qualification"):
        validate(device_value=device_value)


def test_pin_source_and_three_lpp_contracts_fail_closed():
    device_value = device()
    device_value["terminals"]["G"]["drawing_layers"] = []
    with pytest.raises(PdkUnavailable, match="drawing_layers"):
        validate(device_value=device_value)

    device_value = device()
    device_value["terminals"]["G"]["requires_contact_to"] = None
    with pytest.raises(PdkUnavailable, match="requires_contact_to"):
        validate(device_value=device_value)

    device_value = device()
    device_value["terminals"]["D"]["routing_lpp"] = "M3/drawing"
    with pytest.raises(PdkUnavailable, match="routing_lpp"):
        validate(device_value=device_value)


def test_publish_lpp_must_be_in_the_pin_group():
    device_value = device()
    device_value["terminals"]["D"]["publish"]["lpp"] = "m1_drawing"
    with pytest.raises(PdkUnavailable, match="publish.lpp"):
        validate(device_value=device_value)

    device_value = device()
    device_value["terminals"]["D"]["publish"]["lpp"] = "po_pin"
    with pytest.raises(PdkUnavailable, match="pin_lpp_map"):
        validate(device_value=device_value)


def test_parameter_units_formula_domains_and_body_tie_are_machine_checked():
    device_value = device()
    device_value["parameters"]["w"]["unit"] = "um"
    with pytest.raises(PdkUnavailable, match="finger width"):
        validate(device_value=device_value)

    device_value = device()
    device_value["parameters"]["total_width"]["formula"] = "w * wf * m"
    with pytest.raises(PdkUnavailable, match="total_width"):
        validate(device_value=device_value)

    device_value = device()
    device_value["parameters"]["fingers"].pop("domain")
    with pytest.raises(PdkUnavailable, match="fingers"):
        validate(device_value=device_value)

    device_value = device()
    device_value["parameters"]["model"]["domain"] = {"values": []}
    with pytest.raises(PdkUnavailable, match="domain values"):
        validate(device_value=device_value)

    device_value = device()
    device_value["parameters"]["bodytie_type"].pop("layout_effect")
    with pytest.raises(PdkUnavailable, match="layout_effect"):
        validate(device_value=device_value)


def test_must_connect_constraints_are_required():
    device_value = device()
    device_value.pop("constraints")
    with pytest.raises(PdkUnavailable, match="constraints"):
        validate(device_value=device_value)


def test_multi_finger_semantics_are_declarative_only():
    device_value = device()
    device_value["finger_semantics"] = {
        "topology": "S-D-S",
        "source_pin_count": 2,
        "drain_pin_count": 1,
        "gate_pin_count": 2,
    }
    validate(device_value=device_value)
    device_value["finger_semantics"]["bbox"] = [0, 0, 1, 1]
    with pytest.raises(PdkUnavailable, match="unsupported fields"):
        validate(device_value=device_value)


def test_unavailable_macro_records_a_reason_without_layout_geometry():
    device_value = {
        "source": "layout_probe",
        "depends_on": ["nch_25_mac_cdf"],
        "unit": "m",
        "master": {"library": "demo", "cell": "nmos_mac", "view": "unknown"},
        "pcells": ["nmos_mac"],
        "pcell_views": {"nmos_mac": ["symbol", "auLvs"]},
        "variant": "macro",
        "layout_available": False,
        "layout_unavailable_reason": "layout view is not installed",
        "x_layout_ready": False,
    }
    validate_device(device_value)


def test_layout_document_must_be_an_object():
    with pytest.raises(PdkUnavailable, match="object"):
        validate_device([])


def test_package_document_reads_draft_layout_documents(tmp_path):
    from pdk_standard_fixture import package

    from cadai.pdk_standard.jsonio import sha
    from cadai.pdk_standard.package import Package

    current, _, _, _ = package(tmp_path)
    root = current.base.root
    process_value, device_value = process(), device()
    process_value["source"] = "capture"
    device_value["source"] = "capture"
    portable_values = {
        "l": 2.8e-7, "fingers": 1, "m": 1, "model": "nmos",
        "bodytie_type": "Detached", "bulkConnectL": False,
    }
    for name, value in portable_values.items():
        row = device_value["parameters"][name]
        row.pop("cdf")
        row["value"] = value
    manifest = json.loads((root / "package.json").read_text())
    for relative, value in (
        ("layout.json", process_value),
        ("cell0/layout.json", device_value),
    ):
        path = root / relative
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
        manifest["files"][relative] = sha(path.read_bytes())
    (root / "package.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    )

    reloaded = Package(root)
    assert reloaded.document("layout.json")["unit"] == "m"
    assert reloaded.document("cell0/layout.json")["master"]["cell"] == "nmos"
    reloaded.verify()
