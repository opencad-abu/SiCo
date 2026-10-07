"""Conservative collision checks for orthogonal short stubs and supplied occupied bounds."""

from .circuit_geometry_schema import EPS, bounds, inside, overlap

# A port that drives a device terminal belongs on that terminal's pin line: an
# offset of a grid or two (rounding of a body height) makes the router leave the
# pin horizontally, step sideways and enter again, which reads as a stray bend.
ALIGN_WINDOW_GRIDS = 2
ALIGN_HINT_GRIDS = 4


def geometry_issues(
    plan, instances, ports, pins, wires, clearance, terminal_rows=None, grid=None, review=None,
    port_routing="stub",
):
    issues = []

    def add(code, **fields):
        if len(issues) < 128:
            issues.append({"code": code, **fields})

    placed = list(instances.values())
    for index, item in enumerate(placed):
        for other in placed[index + 1 :]:
            if overlap(item["bbox"], other["bbox"], clearance):
                add("instance_clearance", instances=[item["id"], other["id"]])
    for port in ports:
        for inst in placed:
            if inside(port["xy"], inst["bbox"]):
                add("port_on_instance", port=port["name"], instance=inst["id"])
    # Every wire segment is horizontal or vertical; its degenerate bbox describes it exactly.
    segments = [
        (wire, a, b)
        for wire in wires
        for a, b in zip(wire["points"], wire["points"][1:])
    ]
    for index, (wire, a, b) in enumerate(segments):
        box = bounds([a, b])
        endpoint = wire["endpoint"]
        owners = {
            endpoint.get("instance"),
            wire.get("end_endpoint", {}).get("instance"),
            *wire.get("owners", []),
        }
        if wire.get("template_segment"):
            owners = set(wire["owners"])
        for inst in placed:
            if inst["id"] not in owners and overlap(box, inst["bbox"]):
                add("stub_hits_instance", endpoint=endpoint, instance=inst["id"])
        for pin in pins:
            if pin["endpoint"] != endpoint and pin["net"] != wire["net"] and inside(pin["xy"], box):
                add("stub_hits_other_net_pin", endpoint=endpoint, pin=pin["endpoint"])
        for other_wire, c, d in segments[index + 1 :]:
            if other_wire["net"] != wire["net"] and overlap(box, bounds([c, d])):
                add(
                    "different_net_stubs_touch",
                    endpoints=[endpoint, other_wire["endpoint"]],
                )
    pin_region_issues(plan, placed, ports, clearance, add, review)
    if terminal_rows is not None and port_routing == "route":
        port_row_issues(ports, terminal_rows, grid, add, review)
    return issues


def _inward_escape(xy, body):
    """Escape direction that points from a boundary port into the circuit."""
    if xy[0] < body[0][0] - EPS:
        return "right"
    if xy[0] > body[1][0] + EPS:
        return "left"
    if xy[1] > body[1][1] + EPS:
        return "down"
    if xy[1] < body[0][1] - EPS:
        return "up"
    return None  # inside the body footprint: other checks own that case


def pin_region_issues(plan, placed, ports, clearance, add, review=None):
    if not placed:
        return
    body = bounds([p for inst in placed for p in inst["bbox"]])
    for port in ports:
        if port.get("placement_source") == "template":
            continue  # Template sides/order override the default band convention.
        direction = port["direction"]
        if direction in {"input", "inputOutput"} and port["xy"][0] >= body[0][0] - EPS:
            add("port_not_left", port=port["name"])
        if direction == "output" and port["xy"][0] <= body[1][0] + EPS:
            add("port_not_right", port=port["name"])
        if port.get("role") in {"power", "ground"} and port["xy"][0] >= body[0][0] - EPS:
            add("port_not_left", port=port["name"])
        # The reference style draws the note toward the circuit (the arrow and its
        # short wire point into the block, the label sits inside). An escape that
        # points away is legal - the escape only paints the wire - but it is worth
        # a review note: the pin note lands outside the block and a routed net
        # leaves the circuit before coming back.
        required = _inward_escape(port["xy"], body)
        if required and port["escape"] != required and review is not None:
            review.append(
                {
                    "code": "port_stub_points_outward",
                    "port": port["name"],
                    "escape": port["escape"],
                    "preferred": required,
                }
            )
    # Explicit PG roles belong to the band check below; the historical
    # direction-only rule applies to the remaining signal ports.
    defaults = [p for p in ports if p.get("placement_source") != "template"]
    signal = [p for p in defaults if p.get("role") not in {"power", "ground"}]
    inputs = [p["xy"][1] for p in signal if p["direction"] == "input"]
    bidir = [p["xy"][1] for p in signal if p["direction"] == "inputOutput"]
    if inputs and bidir and min(bidir) <= max(inputs) + EPS:
        add("input_output_pins_not_above_inputs")
    _role_band_issues(defaults, add)
    if plan["spec"]["kind"] == "testbench":
        dut = [p for p in placed if p["role"] == "dut"]
        dut_box = bounds([p for item in dut for p in item["bbox"]])
        for item in placed:
            if item["role"] == "stimulus" and item["bbox"][1][0] >= dut_box[0][0] - clearance - EPS:
                add("stimulus_not_left_of_dut", instance=item["id"])
            if item["role"] == "load" and item["bbox"][0][0] <= dut_box[1][0] + clearance + EPS:
                add("load_not_right_of_dut", instance=item["id"])


# Left-edge band order (top to bottom): power, bidirectional, input, ground.
# Power stays on top and ground at the bottom; explicit PG roles win over the
# direction-derived slot so a ground-role iopin still lands at the bottom.
ROLE_BANDS = {"power": 1, "ground": 4}


def _band(port):
    role = port.get("role")
    if role in ROLE_BANDS:
        return ROLE_BANDS[role]
    if port["direction"] == "inputOutput":
        return 2
    if port["direction"] == "input":
        return 3
    return None


def _role_band_issues(ports, add):
    """Reject explicit PG roles that break the documented left-edge order."""
    bands = {}
    for port in ports:
        band = _band(port)
        if band is not None:
            bands.setdefault(band, []).append(port)
    ordered = sorted(bands)
    for higher in ordered:
        for lower in ordered:
            if higher >= lower:
                continue
            if not any(
                p.get("role") in ROLE_BANDS for p in bands[higher] + bands[lower]
            ):
                continue  # direction-only pairs keep their historical code
            for upper in bands[higher]:
                for lower_port in bands[lower]:
                    if upper["xy"][1] <= lower_port["xy"][1] + EPS:
                        add(
                            "port_role_band_order",
                            upper=upper["name"],
                            lower=lower_port["name"],
                        )
                        break
                else:
                    continue
                break


def port_row_issues(ports, terminal_rows, grid, add, review=None):
    """Require a port that drives a terminal to sit on that terminal's pin line.

    ``terminal_rows`` maps a net to ``[(world y, "Instance.TERMINAL"), ...]``.
    An offset of one or two grids is a rounding artefact of the body height, not
    a drawing decision: the router leaves the pin horizontally, steps sideways and
    enters again, which is the stray bend engineers notice first. Rows are matched
    top-down inside each column, so the documented band order is never traded for
    a straight wire; anything further away stays a non-blocking hint.
    """
    grid = grid or 0.0625
    window = ALIGN_WINDOW_GRIDS * grid
    hint = ALIGN_HINT_GRIDS * grid
    for side, inward in (("left", "right"), ("right", "left")):
        column = sorted(
            (port for port in ports if port.get("escape") == inward
             and port.get("placement_source") != "template"),
            key=lambda port: (-port["xy"][1], port["name"]),
        )
        ceiling = None
        for port in column:
            best = None
            for value, label in sorted(terminal_rows.get(port["net"]) or []):
                # Compare on the schematic grid: a port can only sit on a grid line,
                # and the placement tool snaps its rows the same way.
                target = round(value / grid) * grid
                if ceiling is not None and target >= ceiling - EPS:
                    continue
                offset = target - port["xy"][1]
                if abs(offset) > hint:
                    continue
                if best is None or abs(offset) < abs(best[0] - port["xy"][1]):
                    best = (target, label, offset, value)
            if best is None:
                continue
            ceiling = best[0]
            if abs(best[2]) <= EPS:
                continue
            if abs(best[2]) <= window + EPS:
                add(
                    "port_off_terminal_row",
                    port=port["name"],
                    terminal=best[1],
                    y=port["xy"][1],
                    aligned_y=best[0],
                    terminal_y=best[3],
                )
            elif review is not None:
                review.append(
                    {
                        "code": "port_off_terminal_row_hint",
                        "port": port["name"],
                        "terminal": best[1],
                        "offset": best[2],
                        "aligned_y": best[0],
                    }
                )
