"""Deterministic gpdk045 gate geometry; coordinates are schematic user units."""


def port(name, direction, xy):
    return [name, direction, xy]


def device(name, cell, xy, connections, properties=(), library="gpdk045"):
    return [name, library, cell, xy, "R0", list(properties), list(connections.items())]


def gate_topology(gate):
    nand = gate == "NAND2"
    ports = [port("VDD", "inputOutput", [-1, 2]), port("VSS", "inputOutput", [-1, 1.5]),
             port("A", "input", [-1, -0.375]), port("Y", "output", [3, 0.375])]
    devices = [device("MP0", "pmos1v", [0, 1], dict(G="A", S="VDD", D="Y", B="VDD")),
               device("MN0", "nmos1v", [0, -1], dict(G="A", S="NINT" if nand else "VSS", D="Y", B="VSS"))]
    wires = [["VDD", ["MP0", "S"], [0.25, 1.625], [0.75, 1.625]],
             ["VDD", ["MP0", "B"], [0.75, 1], [0.75, 1.625]],
             ["Y", ["MP0", "D"], [0.25, 0.375], ["MN0", "D"]],
             ["Y", [0.25, 0.375], [3, 0.375]],
             ["A", ["MP0", "G"], [-0.5, 1], [-0.5, -1], ["MN0", "G"]],
             ["A", [-1, -0.375], [-0.5, -0.375]],
             ["VDD", [-1, 2], [-0.5, 2]], ["VSS", [-1, 1.5], [-0.5, 1.5]]]
    bottom = -2.625 if nand else -1.625
    wires += [["VSS", ["MN0", "B"], [0.75, -1], [0.75, bottom], [0.25, bottom]]]
    if nand:
        ports += [port("B", "input", [-1, -2])]
        devices += [device("MP1", "pmos1v", [2, 1], dict(G="B", S="VDD", D="Y", B="VDD")),
                    device("MN1", "nmos1v", [0, -2], dict(G="B", S="VSS", D="NINT", B="VSS"))]
        wires += [["VDD", ["MP1", "S"], [2.25, 1.625], [0.75, 1.625]],
                  ["VDD", ["MP1", "B"], [2.75, 1], [2.75, 1.625], [2.25, 1.625]],
                  ["Y", ["MP1", "D"], [2.25, 0.375]],
                  ["NINT", ["MN0", "S"], ["MN1", "D"]],
                  ["VSS", ["MN1", "S"], [0.25, bottom]],
                  ["VSS", ["MN1", "B"], [0.75, -2]],
                  ["B", ["MP1", "G"], [1.5, 1], [1.5, 1.25]],
                  ["B", ["MN1", "G"], [-1, -2]]]
    else:
        wires += [["VSS", ["MN0", "S"], [0.25, bottom]]]
    symbol_ports = [port("VDD", "inputOutput", [-0.25, 0.75]),
                    port("VSS", "inputOutput", [-0.25, 0.375]),
                    port("A", "input", [-0.25, -0.375]), port("Y", "output", [1.25, 0])]
    if nand:
        symbol_ports += [port("B", "input", [-0.25, -0.75])]
    return {"devices": devices, "ports": ports, "wires": wires, "symbol_ports": symbol_ports}


def testbench_topology(library, cell, gate):
    nand = gate == "NAND2"
    dut_name = "I_" + cell + "_0"
    dut_nets = dict(VDD="VDD", VSS="gnd!", A="A", Y="Y")
    if nand:
        dut_nets["B"] = "B"
    devices = [device(dut_name, cell, [3, 1], dut_nets, library=library),
               device("VDD0", "vdc", [0, 2], dict(PLUS="VDD", MINUS="gnd!"), [["vdc", "1"]], "analogLib"),
               device("CLOAD", "cap", [7, 1], dict(PLUS="Y", MINUS="gnd!"), [["c", "1f"]], "analogLib")]
    for name, y, period, delay, width in [("A", 0, "4n", "2n", "1.98n")] + (
            [("B", -2, "2n", "1n", "0.98n")] if nand else []):
        props = [["vdc", "0"], ["v1", "0"], ["v2", "1"], ["td", delay],
                 ["tr", "10p"], ["tf", "10p"], ["pw", width], ["per", period]]
        devices += [device("V"+name, "vpulse", [0, y], dict(PLUS=name, MINUS="gnd!"), props, "analogLib")]
    # Labelled source stubs avoid global input names and make each source explicit.
    wires = [["Y", [dut_name, "Y"], [4.75, 1]],
             ["Y", ["CLOAD", "PLUS"], [7, 1.75]],
             ["gnd!", ["CLOAD", "MINUS"], [7, 0]],
             ["VDD", [dut_name, "VDD"], [2.25, 1.75]],
             ["gnd!", [dut_name, "VSS"], [2.25, 1.375]],
             ["A", [dut_name, "A"], [2.25, 0.625]]]
    if nand:
        wires += [["B", [dut_name, "B"], [2.25, 0.25]]]
    # Sources' plus/minus anchors are resolved from installed masters at runtime.
    for name, y, net in [("VDD0", 2, "VDD"), ("VA", 0, "A")] + ([("VB", -2, "B")] if nand else []):
        devices += [device("G_"+name, "gnd", [0, y-0.75], {"gnd!": "gnd!"}, library="analogLib")]
        wires += [[net, [name, "PLUS"], [0, y+0.75]],
                  ["gnd!", [name, "MINUS"], ["G_"+name, "gnd!"]]]
    return {"devices": devices, "ports": [], "wires": wires}
