"""Project acceptance inputs: archived INVX1 -> selected SMIC devices, never core defaults."""

import json

from circuit_smic28_fixture import selected

from cadai.circuit_spec_schema import digest
from cadai.template_catalog import TemplateCatalog

INV_REF = "tpl_663d98e78bf9b162c6b79bb96ab8372b85d519cc1fc7302b0b7891f95ca5284c"
DUT_CELL = "archived_inv"


def migration_input(bridge, workspace):
    record = TemplateCatalog(workspace=workspace).get(INV_REF)
    assert record["source"]["lib"] == "gsclib045" and record["source"]["cell"] == "INVX1"
    # This is an explicit project selection reviewed for this single topology.
    choices = {"d0": ("n09_ckt", "MN", "1u"), "d1": ("p09_ckt", "MP", "2u")}
    libpath = bridge.skill('ddGetObjReadPath(ddGetObj("smic28hkmg"))')
    masters, devices, geometry = [], [], []
    for source in record["topology"]["devices"]:
        key = source["id"]
        cell, name, width = choices[key]
        master, geo = selected(
            bridge, cell, "smic28hkmg", libpath, key, extension="test:smic28:" + cell + ":v1"
        )
        params = dict(l="60n", fingers="2", w=width, m="1")
        masters.append(master)
        devices.append(
            dict(
                source_device=key,
                instance=key,
                master=key,
                name=name,
                parameters=params,
                terminal_map={p["name"]: p["name"] for p in source["pins"]},
            )
        )
        geo["parameters_digest"] = digest(params)
        geometry.append(geo)
    nets = {n["source_name"]: n["id"] for n in record["topology"]["nets"]}
    bindings = dict(
        schema="cad.circuit.bindings.v1",
        project_ref="project:archived-inv-smic28",
        snapshot_ref="snapshot:selected-static-mos-v1",
        source_ref="test-only-selected-native-dd-db-cdf",
        evidence_kind="session_capture",
        masters=masters,
    )
    return (
        dict(
            template_ref=INV_REF,
            target=dict(library="smicFlowTest", cell=DUT_CELL, view="schematic"),
            bindings=bindings,
            devices=devices,
            net_map={nets["A"]: "IN", nets["Y"]: "OUT", nets["VDD!"]: "VDD", nets["VSS!"]: "VSS"},
            port_map=dict(A="IN", Y="OUT", VDD="VDD", VSS="VSS"),
            net_scope_map={nets["VDD!"]: "local", nets["VSS!"]: "local"},
        ),
        geometry,
        record,
    )


def geometry_input(prepared, rows):
    reference = prepared["plan"]["placement"]["template_reference"]
    assert not reference["gaps"]
    # Explicit project layout choice: preserve source relative placement/orientation,
    # enlarge spacing 2x, translate; native target anchors are used for every stub.
    layout = dict(
        instances={
            p["id"]: dict(
                xy=[2 * p["relative_xy"][0], 2 * p["relative_xy"][1] - 1.125],
                orientation=p["source_orientation"],
            )
            for p in reference["instances"]
        },
        ports={
            name: dict(xy=xy, escape=escape)
            for name, xy, escape in [
                ("IN", [-3, -2], "right"),
                ("VDD", [-3, -3], "right"),
                ("VSS", [-3, -4], "right"),
                ("OUT", [4, 0], "left"),
            ]
        },
        anchor_choices={},
        grid=0.0625,
        stub_length=0.75,
        clearance=0.125,
    )
    bindings = prepared["plan"]["bindings"]
    return dict(
        spec=prepared["spec"],
        bindings=bindings,
        template_use=prepared["template_use"],
        preview_digest=prepared["preview_digest"],
        layout=layout,
        geometry=dict(
            schema="cad.circuit.geometry.v1",
            project_ref=bindings["project_ref"],
            binding_snapshot=bindings["snapshot_ref"],
            binding_digest=prepared["plan"]["binding_digest"],
            source_ref="test-only-selected-symbol-geometry",
            evidence_kind="session_capture",
            coordinate_system="master_local_schematic_uu",
            instances=rows,
        ),
    )


def simulation_recipe(pdk):
    return dict(
        schema="cad.simulation.recipe.v1",
        target=dict(library="smicFlowTest", cell="archived_inv_sim", view="maestro"),
        variables={},
        temperature_c=27,
        tests=[
            dict(
                name="switching",
                design=dict(library="smicFlowTest", cell="archived_inv_tb", view="schematic"),
                simulator="spectre",
                variables={},
                models=[
                    dict(path=str(pdk / "models/spectre/l0028hk_io25_v1p0_spe.lib"), section="TT")
                ],
                analyses=[
                    dict(name="tran", options=dict(stop="5n", maxstep="5p", errpreset="moderate"))
                ],
                outputs=[
                    dict(name=n, kind="expression", value=e, plot=True, save=True)
                    for n, e in [
                        ("high", 'value(VT("/OUT") 0.5e-9)'),
                        ("low", 'value(VT("/OUT") 1.5e-9)'),
                        ("input_low", 'value(VT("/IN") 0.5e-9)'),
                        ("input_high", 'value(VT("/IN") 1.5e-9)'),
                    ]
                ]
                + [
                    dict(name=n, kind="signal", value="/" + n, plot=True, save=True)
                    for n in ("IN", "OUT")
                ],
                switch_views=["spectre", "schematic"],
                stop_views=["spectre"],
            )
        ],
    )


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def verify_generated_views(bridge, project, dut, symbol, tb, setup, call):
    netlists = list((project / "simulation").rglob("netlist/input.scs"))
    text = next(
        p.read_text() for p in netlists if "I_archived_inv_0 (IN OUT VDD 0)" in p.read_text()
    )
    for expected in [
        "MN (OUT IN VSS VSS) n09_ckt w=1u l=60n nf=2",
        "MP (OUT IN VDD VDD) p09_ckt w=2u l=60n nf=2",
        "VSUP (VDD 0) vsource dc=0.9",
        "CL (OUT 0) capacitor c=5f",
    ]:
        assert expected in text
    assert "VDD!" not in text and "VSS!" not in text and "gpdk045" not in text
    (project / "verified-netlist.scs").write_text(text)
    verify_view_readback(bridge, project, dut, symbol, tb, setup, call)


def verify_view_readback(bridge, project, dut, symbol, tb, setup, call):
    bridge.skill(
        'let((v r) v=dbOpenCellViewByType("smicFlowTest" "archived_inv_tb" '
        '"schematic" "" "r") r=aiCircuitJson(list('
        'aiCreatedCircuits["created:tb"][\'snapshot] aiCrSnapshot(v))) dbClose(v) '
        "aiWriteTextFile(" + json.dumps(str(project / "tb-snapshot-comparison.json")) + " r))"
    )
    call("inspect_created_circuit", dict(circuit_ref=dut["circuit_ref"]), "dut-inspect")
    call("inspect_template_symbol", dict(symbol_ref=symbol["symbol_ref"]), "symbol-inspect")
    call("inspect_created_circuit", dict(circuit_ref=tb["circuit_ref"]), "tb-inspect")
    call("inspect_simulation_setup", dict(setup_ref=setup["setup_ref"]), "setup-inspect")
    for label, expr in [
        ("dut", 'aiCreatedCircuits["' + dut["circuit_ref"] + "\"]['window]"),
        ("symbol", 'aiTemplateSymbols["' + symbol["symbol_ref"] + "\"]['window]"),
        ("tb", 'aiCreatedCircuits["' + tb["circuit_ref"] + "\"]['window]"),
    ]:
        # Screenshot is additional visual evidence; view owners remain untouched.
        bridge.skill(
            "hiExportImage(?window "
            + expr
            + " ?fileName "
            + json.dumps(str(project / (label + ".png")))
            + " ?width 1200 ?height 900)"
        )
