"""Selected native probes and explicit SMIC acceptance data; no PDK producer dependency."""

from circuit_create_support import IC_LIBS, sample_master

from cadai.circuit_geometry import preview_geometry
from cadai.circuit_spec_plan import preview_circuit
from cadai.circuit_spec_schema import digest


def selected(bridge, cell, library, library_path, key, *, extension=None):
    captured = sample_master(bridge, cell, library)
    master = {
        "id": key,
        "target": dict(library=library, cell=cell, view="symbol"),
        "library_path": str(library_path),
        "revision": digest(captured),
        "kind": "device" if library != "smicFlowTest" else "design",
        "terminals": [dict(name=t[0], direction=t[1]) for t in captured[6]],
        "terminals_complete": True,
        "parameters": [
            dict(
                name=p[0],
                type={
                    "int": "integer",
                    "float": "number",
                    "cyclic": "string",
                    "radio": "string",
                }.get(p[1], p[1]),
                editable=p[0] in ("l", "w", "m", "fingers")
                if extension
                else p[1] not in ("cyclic", "radio"),
            )
            for p in captured[5] or []
        ],
        "parameters_complete": True,
        "callbacks": dict(status="required", extension_ref=extension)
        if extension
        else dict(status="none"),
    }
    # Geometry comes from this saved static symbol. Text and selection figures do not
    # define its electrical body. This fixture uses the standard pin layer; the
    # native verifier independently excludes the actual terminal pin figures.
    boxes = [
        s[2]
        for s in captured[7]
        if s[0] not in ("label", "text")
        and s[1] not in (["instance", "drawing"], ["pin", "drawing"])
    ]
    box = [
        [min(b[0][k] for b in boxes) for k in (0, 1)],
        [max(b[1][k] for b in boxes) for k in (0, 1)],
    ]
    terms = []
    for term in captured[6]:
        anchors = []
        for i, pin in enumerate(term[2]):
            xy = [sum(c[k] for c in pin[1]) / 2 for k in (0, 1)]
            if library == "smic28hkmg":
                esc = {"G": "left", "B": "right"}.get(term[0], "up" if xy[1] > 0 else "down")
            elif library == "basic":
                esc = "up"
            elif library == "analogLib":
                esc = "up" if term[0] == "PLUS" else "down"
            else:
                esc = "right" if term[1] == "output" else "left"
            anchors.append(dict(id="pin" + str(i), xy=xy, escape=esc))
        terms.append(dict(name=term[0], anchors=anchors))
    return master, dict(
        instance=key, master_revision=master["revision"], occupied_bbox=box, terminals=terms
    )


def inputs(
    bridge,
    path,
    cell,
    rows,
    positions,
    ports,
    nets,
    kind="circuit",
    *,
    ground_links=None,
    symbol_binding=None,
    ground_orientation="R270",
):
    bindings = dict(
        schema="cad.circuit.bindings.v1",
        project_ref="project:smic28-acceptance",
        snapshot_ref="snapshot:selected-test-probe",
        source_ref="test-only-native-dd-db-cdf",
        evidence_kind="session_capture",
        masters=[],
    )
    spec = dict(
        schema="cad.circuit.spec.v1",
        project_ref=bindings["project_ref"],
        binding_snapshot=bindings["snapshot_ref"],
        kind=kind,
        target=dict(library="smicFlowTest", cell=cell, view="schematic"),
        instances=[],
        nets=[
            dict(
                name=n,
                scope="ground"
                if n == "GND" and ground_links
                else "global"
                if n.endswith("!")
                else "local",
            )
            for n in nets
        ],
        ports=[dict(name=n, direction=d, net=n) for n, d in ports],
        notes=[],
    )
    geos = []
    for key, name, library, master_cell, role, params, conns in rows:
        libpath = (
            path / "library"
            if library == "smicFlowTest"
            else (
                IC_LIBS / "artist/analogLib"
                if library == "analogLib"
                else IC_LIBS / "basic"
                if library == "basic"
                else bridge.skill('ddGetObjReadPath(ddGetObj("smic28hkmg"))')
            )
        )
        extension = "test:smic28:" + master_cell + ":v1" if library == "smic28hkmg" else None
        if symbol_binding and key == "dut":
            master, geo = symbol_binding["master"], dict(symbol_binding["geometry"])
        else:
            master, geo = selected(bridge, master_cell, library, libpath, key, extension=extension)
        bindings["masters"].append(master)
        geo["parameters_digest"] = digest(params)
        geos.append(geo)
        inst = dict(
            id=key, master=key, role=role, parameters=params, connections=conns, unconnected={}
        )
        if name:
            inst["name"] = name
        spec["instances"].append(inst)
    preview = preview_circuit(spec, bindings)
    geometry = dict(
        schema="cad.circuit.geometry.v1",
        project_ref=bindings["project_ref"],
        binding_snapshot=bindings["snapshot_ref"],
        binding_digest=preview["plan"]["binding_digest"],
        source_ref="test-only-selected-symbol-geometry",
        evidence_kind="session_capture",
        coordinate_system="master_local_schematic_uu",
        instances=geos,
    )
    layout = dict(
        instances={key: dict(xy=xy, orientation="R0") for key, xy in positions.items()},
        ports={},
        anchor_choices={},
        grid=0.0625,
        stub_length=0.25,
        clearance=0.125,
        routing={"mode": "stub", "decision": {"source": "user", "chosen": "stub"}},
    )
    left_upper, left_lower, right = 2, -2, 0
    for n, direction in ports:
        if direction == "inputOutput":
            xy, esc = [-3, left_upper], "right"
            left_upper += 1
        elif direction == "input":
            xy, esc = [-3, left_lower], "right"
            left_lower -= 1
        else:
            xy, esc = [4, right], "left"
            right += 1
        layout["ports"][n] = dict(xy=xy, escape=esc)
    if ground_links:
        layout["pin_links"] = [
            [dict(instance=key, terminal=term), dict(instance=gnd, terminal="gnd!")]
            for key, term, gnd in ground_links
        ]
        layout["instances"]["gnd_dut"]["orientation"] = ground_orientation
    args = dict(
        spec=spec,
        bindings=bindings,
        preview_digest=preview["preview_digest"],
        geometry=geometry,
        layout=layout,
    )
    geo = preview_geometry(**args)
    assert geo["plan"]["geometry_checks_passed"], geo
    return dict(**args, geometry_plan_digest=geo["geometry_plan_digest"])


def inverter(bridge, path, cell="smic_inv", *, fingers="2", width="1u"):
    return inputs(
        bridge,
        path,
        cell,
        [
            (
                "pull_down",
                "MN",
                "smic28hkmg",
                "n09_ckt",
                "device",
                dict(l="60n", fingers=fingers, w=width, m="1"),
                dict(G="IN", D="OUT", S="VSS", B="VSS"),
            ),
            (
                "pull_up",
                "MP",
                "smic28hkmg",
                "p09_ckt",
                "device",
                dict(l="60n", fingers=fingers, w="2u", m="1"),
                dict(G="IN", D="OUT", S="VDD", B="VDD"),
            ),
        ],
        dict(pull_down=[0, -1], pull_up=[0, 1]),
        [("IN", "input"), ("OUT", "output"), ("VDD", "inputOutput"), ("VSS", "inputOutput")],
        ["IN", "OUT", "VDD", "VSS"],
    )


def testbench(bridge, path, cell="smic_inv_tb", *, dut_cell="smic_inv", symbol_binding=None):
    if symbol_binding:
        terminal = next(t for t in symbol_binding["geometry"]["terminals"] if t["name"] == "VSS")
        anchor = terminal["anchors"][0]
        assert anchor["escape"] == "down", anchor
        vss_xy = anchor["xy"]
        ground_xy, ground_orientation = [vss_xy[0], vss_xy[1] - 0.5], "R0"
    else:
        dut_pins = sample_master(bridge, dut_cell, "smicFlowTest")[6]
        vss_box = next(t for t in dut_pins if t[0] == "VSS")[2][0][1]
        # Ground wire follows the actual generated DUT pin, not the example's old size.
        vss_xy = [sum(p[k] for p in vss_box) / 2 for k in (0, 1)]
        ground_xy, ground_orientation = [vss_xy[0] - 0.5, vss_xy[1]], "R270"
    return inputs(
        bridge,
        path,
        cell,
        [
            (
                "supply",
                "VSUP",
                "analogLib",
                "vdc",
                "stimulus",
                dict(vdc="0.9"),
                dict(PLUS="VDD", MINUS="GND"),
            ),
            (
                "input",
                "VIN",
                "analogLib",
                "vpulse",
                "stimulus",
                dict(v1="0", v2="0.9", td="1n", tr="20p", tf="20p", pw="1n", per="2n"),
                dict(PLUS="IN", MINUS="GND"),
            ),
            (
                "dut",
                None,
                "smicFlowTest",
                dut_cell,
                "dut",
                {},
                dict(IN="IN", OUT="OUT", VDD="VDD", VSS="GND"),
            ),
            ("load", "CL", "analogLib", "cap", "load", dict(c="5f"), dict(PLUS="OUT", MINUS="GND")),
            *[
                ("gnd_" + key, "GND_" + key, "basic", "gnd", "auxiliary", {}, {"gnd!": "GND"})
                for key in ("supply", "input", "dut", "load")
            ],
        ],
        dict(
            supply=[-3, 2],
            input=[-3, -1],
            dut=[0, 0],
            load=[5, 0],
            gnd_supply=[-3, 1.25],
            gnd_input=[-3, -1.75],
            gnd_dut=ground_xy,
            gnd_load=[5, -0.75],
        ),
        [],
        ["IN", "OUT", "VDD", "GND"],
        "testbench",
        symbol_binding=symbol_binding,
        ground_orientation=ground_orientation,
        ground_links=[
            ("supply", "MINUS", "gnd_supply"),
            ("input", "MINUS", "gnd_input"),
            ("dut", "VSS", "gnd_dut"),
            ("load", "MINUS", "gnd_load"),
        ],
    )
