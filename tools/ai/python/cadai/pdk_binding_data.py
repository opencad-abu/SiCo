"""Source-preserving conversion; all project choices arrive as explicit arguments."""

from .circuit_geometry_schema import BOX, POINT, inside
from .circuit_spec_schema import CircuitSpecError, unique, validate
from .pdk_normalize import raw_value
from .pdk_schema import SECTIONS


def require(condition, message):
    if not condition:
        raise CircuitSpecError("PDK binding: " + message)


def binding_ref(args, context, device, body):
    """Identify a session-bound selection by content, not its discovery handle."""
    from .circuit_spec_schema import digest

    identity = dict(
        choices={key: value for key, value in args.items() if key != "snapshot_ref"},
        context={key: context[key] for key in
                 ("session_ref", "session_generation", "project_ref", "virtuoso_version")},
        target=device["target"], library_path=device["library"]["resolved_path"], body=body,
    )
    return "pdk-adapter:" + digest(identity)


def read_device(session, args):
    query = {k: args[k] for k in ("snapshot_ref", "device_ref")}
    # The adapter always needs the complete effective CDF, not only the design interface tier.
    query.update(sections=list(SECTIONS), page_size=100, tier="all")
    device, ctx, seen = None, None, set()
    for _ in range(256):
        page = session.call("get_pdk_device", query)
        current = page["device"]
        require(current["revision"] == args["revision"],
                "selected revision differs: received " + args["revision"]
                + "; expected " + current["revision"]
                + ". Use get_pdk_device.device.revision for " + args["device_ref"]
                + " in " + args["snapshot_ref"] + ", not search summary_revision; select again")
        if device is None:
            ctx = page["context"]
            device = {**current, **{s: {**current[s], "items": []} for s in SECTIONS}}
        require(ctx == page["context"], "context changed between detail pages")
        for section in SECTIONS:
            require(current[section]["status"] == "complete", section + " metadata incomplete")
            require(current[section]["count"] == device[section]["count"], "page counts differ")
            device[section]["items"].extend(current[section]["items"])
        cursor = page["page"]["next_cursor"]
        if cursor is None:
            for section in SECTIONS:
                require(
                    len(device[section]["items"]) == device[section]["count"],
                    section + " detail pages incomplete",
                )
            return device, ctx
        require(cursor not in seen, "repeated detail cursor")
        seen.add(cursor)
        query["cursor"] = cursor
    raise CircuitSpecError("PDK detail pagination exceeds adapter budget")


def parameter_rows(device, args):
    policies = unique(args["writable_parameters"], "name", "writable parameters")
    params = unique(device["parameters"]["items"], "name", "effective CDF")
    require(not set(policies) - set(params), "project policy names missing CDF parameters")
    extension = args.get("extension_ref")
    callbacks = device["callbacks"]["items"]
    require(all(c["presence"] in {"present", "absent"} for c in callbacks), "unknown callbacks")
    needs_extension = any(c["presence"] == "present" for c in callbacks)
    require(not needs_extension or extension, "CDF callbacks/hooks require installed callback support")
    rows, omitted = [], []
    types = {
        "string": "string",
        "int": "integer",
        "float": "number",
        "boolean": "boolean",
        "cyclic": "string",
        "radio": "string",
    }
    for name, param in params.items():
        typ = param["cdf_type"]
        if typ == "button":
            require(name not in policies, "CDF button is an action, not a writable value")
            omitted.append(name)
            continue
        require(typ in types, "unsupported CDF type for " + name)
        if name in policies:
            require(typ not in {"cyclic", "radio"} or extension == "builtin:cdf-callbacks:v1",
                    "cyclic/radio writes require the built-in CDF executor")
            cond, policy = param["editable"], policies[name]["policy"]
            raw = cond.get("raw") or {}
            if extension == "builtin:cdf-callbacks:v1":
                pass  # editable/display control GUI widgets, not structured CDF writes.
            elif policy == "static_true":
                require(
                    cond["status"] == "known" and cond["value"] is True,
                    name + " editable is not statically true",
                )
            elif policy == "absent_condition":
                require(
                    raw.get("status") == "known" and raw.get("type") == "nil",
                    name + " editable condition is not known raw nil",
                )
            else:
                require(extension, name + " requires an installed project extension")
        row = dict(name=name, type=types[typ], editable=name in policies)
        # Non-writable choices remain available in the frozen source detail. CDF cyclic
        # choices may legitimately include whitespace, which is not a circuit input value.
        choices = raw_value(param.get("choices")) if name in policies else None
        if choices and typ == "boolean":
            choices = [False if v is None else True if v == "t" else v for v in choices]
        if choices:
            row["choices"] = choices
        rows.append(row)
    callback = (
        dict(status="required", extension_ref=extension) if extension else dict(status="none")
    )
    if extension == "builtin:cdf-callbacks:v1":
        order = args.get("callback_order")
        require(isinstance(order, list) and len(order) == len(set(order))
                and set(order) == set(policies),
                "callback_order must list each writable parameter exactly once in edit order")
        callback["order"] = order
    return rows, callback, omitted


def terminal_map(device, args):
    """D5: CDF port -> model-deck terminal, confirmed by CAD before it drives netlist order."""
    ports = unique(device["ports"]["items"], "name", "ports")
    crosscheck = device.get("port_crosscheck") or {}
    deck = [name for name in (crosscheck.get("deck") or []) if isinstance(name, str)]
    mapping = args.get("netlist_terminal_map")
    if mapping is None:
        if deck and not crosscheck.get("same_set"):
            require(
                False,
                "deck terminals (" + ", ".join(deck) + ") do not match the CDF ports ("
                + ", ".join(ports) + ") by name; pass the CAD-confirmed netlist_terminal_map",
            )
        return {}, "matched_by_name" if (deck and crosscheck.get("same_set")) else "unverified"
    require(isinstance(mapping, dict) and mapping, "netlist_terminal_map must be a non-empty object")
    require(set(mapping) == set(ports), "netlist_terminal_map must cover exactly the CDF ports")
    values = list(mapping.values())
    require(all(isinstance(value, str) and value for value in values),
            "netlist_terminal_map values must be terminal names")
    require(len(set(values)) == len(values), "netlist_terminal_map must be one-to-one")
    if deck:
        require(set(values) == set(deck),
                "netlist_terminal_map must permute the model deck terminals")
    return dict(mapping), "confirmed"


def deck_order(terms, mapping, deck):
    """Netlist terminal order follows the model deck once a mapping is confirmed (D5)."""
    order = {name: index for index, name in enumerate(deck)}
    if not mapping:
        return terms
    return sorted(terms, key=lambda row: order.get(mapping.get(row.get("name"), ""), len(order)))


def geometry_rows(device, args):
    source = device["geometry"]
    require(device["master_state"]["modified"] is False, "saved master required")
    require(device["identity"]["items"][0]["view_type"] == "schematicSymbol", "symbol required")
    require(
        source.get("parameterized_master") is False
        and source.get("hierarchical_instance_count") == 0,
        "static flat master required",
    )
    require(
        source["coordinate_system"] == "master_local_schematic_user_units", "unknown coordinates"
    )
    box = raw_value(source["bbox"])
    validate(box, BOX, "master bbox")
    require(all(box[0][k] < box[1][k] for k in (0, 1)), "nonpositive master bounds")
    ports = unique(device["ports"]["items"], "name", "ports")
    figures = unique(source["items"], "figure_ref", "pin figures")
    escapes = unique(args["pin_escapes"], "figure_ref", "pin escapes")
    require(set(figures) == set(escapes), "explicit escape required for every pin figure")
    require(all(f["terminal"] in ports for f in figures.values()), "orphan pin figure")
    terms, geometry = [], []
    for name, port in ports.items():
        require(port["width"] == 1, "bus bundles require a later adapter")
        expr = port["net_expression"]
        require(
            expr.get("status") == "known" and expr.get("type") == "nil",
            "inherited terminal expressions unsupported",
        )
        selected = sorted(
            (f for f in figures.values() if f["terminal"] == name),
            key=lambda f: (f["pin_index"], f["figure_index"]),
        )
        require(
            len(selected) == port["pin_count"] and 1 <= len(selected) <= 8,
            "exactly one rectangular figure per physical pin required",
        )
        require(
            [f["pin_index"] for f in selected] == list(range(port["pin_count"]))
            and all(f["figure_index"] == 0 for f in selected),
            "multi-figure pin unsupported",
        )
        anchors = []
        for figure in selected:
            require(figure["status"] == "complete", "incomplete pin figure")
            bbox, typ = figure["bbox"], figure["shape_type"]
            validate(bbox, BOX, "pin bbox")
            require(all(bbox[0][k] < bbox[1][k] for k in (0, 1)), "degenerate pin bbox")
            corners = {(x, y) for x in (bbox[0][0], bbox[1][0]) for y in (bbox[0][1], bbox[1][1])}
            points = raw_value(figure["raw"].get("points"))
            if typ == "polygon":
                require(
                    isinstance(points, list) and len(points) == 4, "rectangular polygon required"
                )
                for point in points:
                    validate(point, POINT, "pin vertex")
                require(set(map(tuple, points)) == corners, "nonrectangular pin polygon")
            else:
                require(typ == "rect", "rectangular pin required")
            xy = [(bbox[0][k] + bbox[1][k]) / 2 for k in (0, 1)]
            require(inside(xy, box), "pin outside full master bbox")
            anchors.append(
                dict(
                    id="pin" + str(figure["pin_index"]),
                    xy=xy,
                    escape=escapes[figure["figure_ref"]]["escape"],
                )
            )
        terms.append(dict(name=name, direction=port["direction"]))
        geometry.append(dict(name=name, anchors=anchors))
    return terms, box, geometry
