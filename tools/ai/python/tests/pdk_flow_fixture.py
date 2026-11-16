"""Explicit SMIC acceptance choices, independent of the production workflow."""

from copy import deepcopy

from template_smic28_fixture import DUT_CELL, INV_REF, geometry_input, simulation_recipe
from test_pdk_binding_gui import selected

from cadai.circuit_spec_schema import digest

PROJECT = "project:formal-pdk-acceptance"


def bindings(records):
    return dict(
        schema="cad.circuit.bindings.v1",
        project_ref=PROJECT,
        snapshot_ref="project:formal-flow-selection",
        source_ref="formal-mcp-adapters",
        evidence_kind="session_capture",
        masters=[r["master"] for r in records],
    )


def select_devices(server):
    choices = [
        (
            "d0",
            "smic28hkmg",
            "n09_ckt",
            dict(G="left", B="right", D="up", S="down"),
            {n: "project_extension" for n in ("l", "w", "m", "fingers")},
        ),
        (
            "d1",
            "smic28hkmg",
            "p09_ckt",
            dict(G="left", B="right", D="down", S="up"),
            {n: "project_extension" for n in ("l", "w", "m", "fingers")},
        ),
        ("supply", "analogLib", "vdc", dict(PLUS="up", MINUS="down"), {"vdc": "absent_condition"}),
        (
            "input",
            "analogLib",
            "vpulse",
            dict(PLUS="up", MINUS="down"),
            {n: "absent_condition" for n in ("v1", "v2", "td", "tr", "tf", "pw", "per")},
        ),
        ("load", "analogLib", "cap", dict(PLUS="up", MINUS="down"), {"c": "absent_condition"}),
        ("ground", "basic", "gnd", {"gnd!": "up"}, {}),
    ]
    records, pages = {}, {}
    for key, lib, cell, escapes, writable in choices:
        ext = "test:smic28:" + cell + ":v1" if lib == "smic28hkmg" else None
        records[key], pages[key] = selected(server, lib, cell, key, escapes, writable, ext)
    return records, pages


def dut_input(record, selected_records):
    records = [selected_records[k] for k in ("d0", "d1")]
    params = {
        "d0": dict(l="60n", fingers="2", w="1u", m="1"),
        "d1": dict(l="60n", fingers="2", w="2u", m="1"),
    }
    nets = {n["source_name"]: n["id"] for n in record["topology"]["nets"]}
    request = dict(
        template_ref=INV_REF,
        target=dict(library="smicFlowTest", cell=DUT_CELL, view="schematic"),
        bindings=bindings(records),
        devices=[
            dict(
                source_device=k,
                instance=k,
                master=k,
                name=name,
                parameters=params[k],
                terminal_map={n: n for n in ("G", "D", "S", "B")},
            )
            for k, name in (("d0", "MN"), ("d1", "MP"))
        ],
        net_map={nets["A"]: "IN", nets["Y"]: "OUT", nets["VDD!"]: "VDD", nets["VSS!"]: "VSS"},
        port_map=dict(A="IN", Y="OUT", VDD="VDD", VSS="VSS"),
        net_scope_map={nets["VDD!"]: "local", nets["VSS!"]: "local"},
    )
    rows = [
        dict(r["geometry"], parameters_digest=digest(params[r["master"]["id"]])) for r in records
    ]
    return request, rows


def dut_geometry(prepared, rows):
    result = geometry_input(prepared, rows)
    result["geometry"]["source_ref"] = "formal-pdk-creator-geometry"
    return result


def tb_input(records, symbol):
    records = {**records, "dut": symbol}
    used = [records[k] for k in ("supply", "input", "load", "ground", "dut")]
    binding = bindings(used)
    rows = [
        ("supply", "supply", "VSUP", "stimulus", dict(vdc="0.9"), dict(PLUS="VDD", MINUS="GND")),
        (
            "input",
            "input",
            "VIN",
            "stimulus",
            dict(v1="0", v2="0.9", td="1n", tr="20p", tf="20p", pw="1n", per="2n"),
            dict(PLUS="IN", MINUS="GND"),
        ),
        ("load", "load", "CL", "load", dict(c="5f"), dict(PLUS="OUT", MINUS="GND")),
        ("dut", "dut", None, "dut", {}, dict(IN="IN", OUT="OUT", VDD="VDD", VSS="GND")),
        *[
            ("gnd_" + k, "ground", "GND_" + k, "auxiliary", {}, {"gnd!": "GND"})
            for k in ("supply", "input", "load", "dut")
        ],
    ]
    instances, geos = [], []
    for key, master, name, role, params, conns in rows:
        inst = dict(
            id=key, master=master, role=role, parameters=params, connections=conns, unconnected={}
        )
        if name:
            inst["name"] = name
        instances.append(inst)
        geos.append(
            dict(records[master]["geometry"], instance=key, parameters_digest=digest(params))
        )
    spec = dict(
        schema="cad.circuit.spec.v1",
        project_ref=PROJECT,
        binding_snapshot=binding["snapshot_ref"],
        kind="testbench",
        target=dict(library="smicFlowTest", cell="archived_inv_tb", view="schematic"),
        instances=instances,
        nets=[
            dict(name=n, scope="ground" if n == "GND" else "local")
            for n in ("IN", "OUT", "VDD", "GND")
        ],
        ports=[],
    )
    positions = dict(supply=[-3, 2], input=[-3, -1], dut=[0, 0], load=[5, 0])
    # Ground origins come from observed selected pins, not remembered master dimensions.
    for key, terminal in (
        ("supply", "MINUS"),
        ("input", "MINUS"),
        ("load", "MINUS"),
        ("dut", "VSS"),
    ):
        anchor = next(t for t in records[key]["geometry"]["terminals"] if t["name"] == terminal)[
            "anchors"
        ][0]
        assert anchor["escape"] == "down"
        ground = records["ground"]["geometry"]["terminals"][0]["anchors"][0]
        positions["gnd_" + key] = [
            positions[key][i] + anchor["xy"][i] - ground["xy"][i] - (0.5 if i == 1 else 0)
            for i in (0, 1)
        ]
    layout = dict(
        instances={k: dict(xy=xy, orientation="R0") for k, xy in positions.items()},
        ports={},
        anchor_choices={},
        grid=0.0625,
        stub_length=0.25,
        clearance=0.125,
        pin_links=[
            [dict(instance=k, terminal=t), dict(instance="gnd_" + k, terminal="gnd!")]
            for k, t in (("supply", "MINUS"), ("input", "MINUS"), ("load", "MINUS"), ("dut", "VSS"))
        ],
    )
    return dict(
        spec=spec,
        bindings=binding,
        layout=layout,
        geometry=dict(
            schema="cad.circuit.geometry.v1",
            project_ref=PROJECT,
            binding_snapshot=binding["snapshot_ref"],
            binding_digest=digest(binding),
            source_ref="formal-pdk-and-owned-symbol",
            evidence_kind="session_capture",
            coordinate_system="master_local_schematic_uu",
            instances=geos,
        ),
    )


def recipe(pdk, config_ref):
    data = simulation_recipe(pdk)
    data["target"]["cell"] = "formal_pdk_comparison"
    direct = data["tests"][0]
    direct["name"] = "direct"
    config = deepcopy(direct)
    config.update(name="configured", config_ref=config_ref)
    config["design"]["view"] = "config"
    data["tests"].append(config)
    return data
