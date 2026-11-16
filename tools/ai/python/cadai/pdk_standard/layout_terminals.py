"""Validate terminal pin sources, routing LPPs and publish contracts."""

from .jsonio import fail
from .layout_common import (
    DIRECTIONS,
    PIN_SHAPE_KINDS,
    PIN_SOURCES,
    enum,
    finite,
    layer_lpp_index,
    lpp,
    nonnegative_int,
    pin_group_members,
    process_layer_id,
    routing_order_index,
    string,
)
from .validate import fields


def _terminal_lpp(row, process, label):
    drawing = row["drawing_layers"]
    if not isinstance(drawing, list) or any(not isinstance(item, str) for item in drawing):
        fail(label + " drawing_layers must be a string array")
    if len(drawing) != len(set(drawing)):
        fail(label + " drawing_layers must be unique")
    for item in drawing:
        if process is not None:
            process_layer_id(process, item, label + " drawing layer")
        else:
            lpp(item, label + " drawing layer")
    routing = row["routing_lpp"]
    contact = row["requires_contact_to"]
    routing_layer = None
    if routing is not None:
        lpp(routing, label + " routing_lpp")
        if drawing and routing not in drawing:
            fail(label + " routing_lpp must be one of drawing_layers")
        if process is not None:
            routing_layer = process_layer_id(process, routing, label + " routing_lpp")
            if routing_layer not in routing_order_index(process):
                fail(label + " routing_lpp is not in routing.layer_order")
    elif row["pin_source"] != "none" and contact is None:
        fail(label + " non-routing terminal requires requires_contact_to")
    if contact is not None:
        lpp(contact, label + " requires_contact_to")
        if process is not None:
            contact_layer = process_layer_id(process, contact, label + " requires_contact_to")
            if contact_layer not in routing_order_index(process):
                fail(label + " requires_contact_to is not routable")
        if routing is not None:
            fail(label + " routing_lpp and requires_contact_to are mutually exclusive")
    order = row["routing_order"]
    if routing is None:
        if order is not None:
            fail(label + " routing_order must be null without routing_lpp")
    else:
        nonnegative_int(order, label + " routing_order")
        if process is not None and order != routing_order_index(process)[routing_layer]:
            fail(label + " routing_order disagrees with routing.layer_order")


def _publish(row, process, label):
    value = row["publish"]
    fields(value, ("lpp", "label"), ("label_inside_shape", "label_layer"))
    string(value["lpp"], label + " publish lpp")
    string(value["label"], label + " publish label")
    if "label_layer" in value:
        string(value["label_layer"], label + " publish label_layer")
        if process is not None and value["label_layer"] not in process["layers"]:
            fail(label + " publish.label_layer references an unknown layer")
    if "label_inside_shape" in value and type(value["label_inside_shape"]) is not bool:
        fail(label + " label_inside_shape must be boolean")
    if process is not None and value["lpp"] not in pin_group_members(process):
        fail(label + " publish.lpp must be a layer_groups.pin member")
    if process is not None and row["drawing_layers"]:
        lpps = layer_lpp_index(process["layers"])
        mapping = process.get("pin_lpp_map", {})
        expected = {
            mapping[lpps[item]] for item in row["drawing_layers"]
            if item in lpps and lpps[item] in mapping
        }
        if expected and value["lpp"] not in expected:
            fail(label + " publish.lpp disagrees with pin_lpp_map")
    if "pin_layer" in row and row["pin_layer"] != value["lpp"]:
        fail(label + " legacy pin_layer disagrees with publish.lpp")
    if "label_inside_shape" in row and "label_inside_shape" in value:
        if row["label_inside_shape"] != value["label_inside_shape"]:
            fail(label + " label_inside_shape values disagree")
    if "labels" in row:
        if not isinstance(row["labels"], list) or not row["labels"]:
            fail(label + " legacy labels must be a non-empty array")
        found = False
        for item in row["labels"]:
            fields(item, ("text", "layer"))
            string(item["text"], label + " label text")
            string(item["layer"], label + " label layer")
            if process is not None and item["layer"] not in process["layers"]:
                fail(label + " label references unknown layer")
            found = found or (item["text"] == value["label"] and item["layer"] == value["lpp"])
        if not found:
            fail(label + " publish label is absent from legacy labels")
    if "min_area" in row:
        finite(row["min_area"], label + " min_area", positive=True)


def validate_terminals(value, process=None, term_order=None):
    """Validate terminals and return ``(names, must_connect_names)``."""
    if not isinstance(value, dict) or not value:
        fail("layout terminals must be a non-empty mapping")
    seen_orders, ordered = set(), []
    for key, row in value.items():
        string(key, "terminal name")
        fields(
            row,
            ("order", "direction", "pin_source", "drawing_layers", "routing_lpp",
             "routing_order", "requires_contact_to", "publish", "must_connect",
             "pin_shape_kind"),
            ("pin_layer", "labels", "label_inside_shape", "min_area"),
        )
        nonnegative_int(row["order"], "terminal order")
        if row["order"] in seen_orders:
            fail("duplicate terminal order")
        seen_orders.add(row["order"])
        enum(row["direction"], DIRECTIONS, "terminal direction")
        enum(row["pin_source"], PIN_SOURCES, "terminal pin_source")
        enum(row["pin_shape_kind"], PIN_SHAPE_KINDS, "terminal pin_shape_kind")
        if type(row["must_connect"]) is not bool:
            fail("terminal must_connect must be boolean")
        if row["pin_source"] == "oa_pin_attribute" and not row["drawing_layers"]:
            fail("oa_pin_attribute terminal requires drawing_layers")
        _terminal_lpp(row, process, "terminal " + key)
        _publish(row, process, "terminal " + key)
        ordered.append((row["order"], key))
    if term_order is not None:
        actual = [name for _, name in sorted(ordered)]
        if actual != list(term_order):
            fail("layout terminal order differs from simulation term_order")
    ties = {name for name, row in value.items() if row["must_connect"]}
    return set(value), ties


def validate_constraints(value, required, terminal_names):
    """Validate device constraints and ensure every implicit tie is declared."""
    if value is None:
        if required:
            fail("must_connect terminals require constraints")
        return
    if not isinstance(value, list) or not value:
        fail("layout constraints must be a non-empty array")
    seen, tied = set(), set()
    for row in value:
        fields(row, ("id", "kind", "terminals", "reason"), ("net",))
        string(row["id"], "constraint ID")
        if row["id"] in seen:
            fail("duplicate layout constraint ID")
        seen.add(row["id"])
        enum(row["kind"], frozenset({"must_connect"}), "constraint kind")
        if not isinstance(row["terminals"], list) or not row["terminals"]:
            fail("constraint terminals must be a non-empty array")
        if any(not isinstance(item, str) or item not in terminal_names
               for item in row["terminals"]):
            fail("constraint references an unknown terminal")
        if len(row["terminals"]) != len(set(row["terminals"])):
            fail("constraint terminals must be unique")
        string(row["reason"], "constraint reason")
        if "net" in row:
            string(row["net"], "constraint net")
        tied.update(row["terminals"])
    missing = required - tied
    if missing:
        fail("must_connect terminals require constraints: " + ", ".join(sorted(missing)))


__all__ = ["validate_constraints", "validate_terminals"]
