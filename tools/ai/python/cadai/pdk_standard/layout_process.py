"""Validate the package-level ``layout.json`` draft (Appendix E base profile)."""

from .jsonio import fail
from .layout_common import (
    LAYER_KINDS,
    LAYER_USAGE,
    digest,
    enum,
    enum_list,
    envelope_fields,
    finite,
    identifier_key,
    nonnegative_int,
    pair,
    positive_int,
    string,
)
from .validate import fields


def _layers(value):
    if not isinstance(value, dict) or not value:
        fail("layers must be a non-empty mapping")
    seen_gds, seen_names = set(), set()
    for key, row in value.items():
        identifier_key(key, "layer ID")
        required = ("name", "purpose", "gds_layer", "gds_datatype", "kind", "usage")
        optional = (
            "gds_name", "direction", "layer_group", "stack_order",
            "min_width", "min_spacing", "max_width", "color", "note",
        )
        fields(row, required, optional)
        string(row["name"], "layer name")
        string(row["purpose"], "layer purpose")
        nonnegative_int(row["gds_layer"], "gds_layer")
        nonnegative_int(row["gds_datatype"], "gds_datatype")
        enum(row["kind"], LAYER_KINDS, "layer kind")
        enum_list(row["usage"], LAYER_USAGE, "layer usage")
        if "gds_name" in row:
            string(row["gds_name"], "gds_name")
        if "direction" in row:
            enum(row["direction"], frozenset({"horizontal", "vertical"}), "layer direction")
        if "layer_group" in row:
            string(row["layer_group"], "layer_group")
        if "stack_order" in row:
            nonnegative_int(row["stack_order"], "stack_order")
        for key_name in ("min_width", "min_spacing", "max_width"):
            if key_name in row:
                finite(row[key_name], key_name, positive=True)
        if "color" in row and (isinstance(row["color"], bool)
                               or not isinstance(row["color"], (str, int))):
            fail("color must be a string or integer")
        if "note" in row:
            string(row["note"], "layer note", allow_empty=True)
        gds = (row["gds_layer"], row["gds_datatype"])
        names = (row["name"], row["purpose"])
        if gds in seen_gds:
            fail("Duplicate GDS layer/datatype mapping")
        if names in seen_names:
            fail("Duplicate layer name/purpose mapping")
        seen_gds.add(gds)
        seen_names.add(names)
    return value


def _layer_groups(value, layers, routing_order):
    if value is None:
        fail("layer_groups must be a mapping")
    if not isinstance(value, dict):
        fail("layer_groups must be a mapping")
    for key, row in value.items():
        identifier_key(key, "layer group ID")
        fields(row, ("members",), ("usage", "order"))
        if not isinstance(row["members"], list) or not row["members"]:
            fail("layer group members must be a non-empty array")
        if any(not isinstance(item, str) for item in row["members"]):
            fail("layer group members must be strings")
        if len(row["members"]) != len(set(row["members"])):
            fail("layer group members must be unique")
        for member in row["members"]:
            if member not in layers:
                fail("Unknown layer group member: " + str(member))
        if "usage" in row:
            enum_list(row["usage"], LAYER_USAGE, "layer group usage")
        if "order" in row:
            nonnegative_int(row["order"], "layer group order")
    groups = value or {}
    for key, row in layers.items():
        group = row.get("layer_group")
        if group is not None and group not in groups:
            fail("Layer references unknown layer_group: " + str(group))
    routing = groups.get("routing")
    if routing is None:
        fail("layout requires layer_groups.routing")
    if set(routing["members"]) != set(routing_order):
        fail("layer_groups.routing must match routing.layer_order")
    pin = groups.get("pin")
    if pin is None:
        fail("layout requires layer_groups.pin")
    for member in pin["members"]:
        if "pin" not in layers[member]["usage"]:
            fail("pin layer group member lacks pin usage: " + member)
    text = groups.get("text")
    if text is not None:
        for member in text["members"]:
            if not set(layers[member]["usage"]) & {"label", "text"}:
                fail("text layer group member lacks label/text usage: " + member)
    marker = groups.get("marker")
    routing_members = set(routing_order)
    pin_members = set(pin["members"])
    if marker is not None:
        for member in marker["members"]:
            row = layers[member]
            if row["kind"] != "marker" and "marker" not in row["usage"]:
                fail("marker layer group member is not a marker: " + member)
            if member in routing_members or member in pin_members:
                fail("marker layer cannot be routing or pin: " + member)
    for key, row in layers.items():
        if row["kind"] == "marker" or "marker" in row["usage"]:
            if key in routing_members or key in pin_members:
                fail("marker layer cannot be used for routing or pins: " + key)


def _vias(value, layers):
    if not isinstance(value, dict):
        fail("vias must be a mapping")
    for key, row in value.items():
        identifier_key(key, "via ID")
        fields(
            row,
            ("cut_layer", "bottom_layer", "top_layer", "cut_size",
             "cut_spacing", "bottom_enclosure", "top_enclosure"),
            ("max_array",),
        )
        for name in ("cut_layer", "bottom_layer", "top_layer"):
            string(row[name], "via " + name)
            if row[name] not in layers:
                fail("Via references unknown layer: " + str(row[name]))
        if layers[row["cut_layer"]]["kind"] != "cut":
            fail("Via cut_layer must have cut kind")
        pair(row["cut_size"], "via cut_size", positive=True)
        finite(row["cut_spacing"], "via cut_spacing", positive=True)
        finite(row["bottom_enclosure"], "via bottom_enclosure", nonnegative=True)
        finite(row["top_enclosure"], "via top_enclosure", nonnegative=True)
        if "max_array" in row:
            if (not isinstance(row["max_array"], list) or len(row["max_array"]) != 2
                    or any(type(item) is not int or item <= 0 for item in row["max_array"])):
                fail("via max_array must be two positive integers")


def _routing(value, layers):
    fields(value, ("track_grid", "wire_widths", "layer_order", "spacings"))
    finite(value["track_grid"], "routing track_grid", positive=True)
    if not isinstance(value["layer_order"], list) or not value["layer_order"]:
        fail("routing layer_order must be a non-empty array")
    if any(not isinstance(item, str) for item in value["layer_order"]):
        fail("routing layer_order must contain strings")
    if len(value["layer_order"]) != len(set(value["layer_order"])):
        fail("routing layer_order must not contain duplicates")
    for layer in value["layer_order"]:
        if layer not in layers:
            fail("routing layer_order references unknown layer: " + str(layer))
        if layers[layer]["kind"] != "conductor":
            fail("routing layer must have conductor kind: " + layer)
    widths = value["wire_widths"]
    if not isinstance(widths, list) or len(widths) != len(value["layer_order"]):
        fail("routing wire_widths must correspond one-to-one with layer_order")
    for width in widths:
        finite(width, "routing wire width", positive=True)
    if not isinstance(value["spacings"], dict):
        fail("routing spacings must be a mapping")
    seen_layers = set()
    for key, row in value["spacings"].items():
        identifier_key(key, "routing spacing ID")
        fields(row, ("layer", "spacing"), ("width",))
        string(row["layer"], "routing spacing layer")
        if row["layer"] not in value["layer_order"]:
            fail("routing spacing references a layer outside layer_order")
        finite(row["spacing"], "routing spacing", positive=True)
        if "width" in row:
            finite(row["width"], "routing spacing width", positive=True)
        seen_layers.add(row["layer"])
    missing = set(value["layer_order"]) - seen_layers
    if missing:
        fail("routing spacings missing layer_order entries: " + ", ".join(sorted(missing)))
    if all(isinstance(layers[layer].get("stack_order"), int)
           for layer in value["layer_order"]):
        expected = sorted(value["layer_order"], key=lambda item: layers[item]["stack_order"])
        if value["layer_order"] != expected:
            fail("routing layer_order must be ordered from low to high")


def _via_stack(vias, routing_order):
    for lower, upper in zip(routing_order, routing_order[1:]):
        if not any(row["bottom_layer"] == lower and row["top_layer"] == upper
                   for row in vias.values()):
            fail("vias must cover adjacent routing layers: " + lower + " -> " + upper)


def _stream(value, database_unit):
    fields(value, ("layer_map_ref", "layer_map_sha", "gds_unit", "precision"))
    string(value["layer_map_ref"], "stream layer_map_ref")
    digest(value["layer_map_sha"], "stream layer_map_sha")
    finite(value["gds_unit"], "stream gds_unit", positive=True)
    precision = finite(value["precision"], "stream precision", positive=True)
    if abs(value["gds_unit"] - 1e-6) > 1e-18:
        fail("stream gds_unit must be one micron in metres")
    if abs(precision - database_unit) > 1e-18:
        fail("stream precision must equal database_unit")
    if abs(value["gds_unit"] / precision - round(value["gds_unit"] / precision)) > 1e-9:
        fail("stream gds_unit must be an integer multiple of precision")


def _pin_lpp_map(value, layers, groups):
    if not isinstance(value, dict) or not value:
        fail("pin_lpp_map must be a non-empty mapping")
    pin = groups.get("pin") if isinstance(groups, dict) else None
    members = set(pin["members"]) if isinstance(pin, dict) else set()
    for source, target in value.items():
        if source not in layers:
            fail("pin_lpp_map references unknown drawing layer: " + str(source))
        if target not in layers:
            fail("pin_lpp_map references unknown pin layer: " + str(target))
        if layers[source]["purpose"] != "drawing":
            fail("pin_lpp_map source must be a drawing layer: " + source)
        if layers[target]["purpose"] != "pin" and target not in members:
            fail("pin_lpp_map target must be a pin layer: " + target)
        if layers[source]["name"] != layers[target]["name"]:
            fail("pin_lpp_map must map a layer to its own pin purpose: " + source)


def _density(value, layers):
    fields(value, ("targets",))
    if not isinstance(value["targets"], list) or not value["targets"]:
        fail("density targets must be a non-empty array")
    for row in value["targets"]:
        fields(row, ("layer", "min", "max"), ("fill_spacing", "fill_cell_ref"))
        string(row["layer"], "density layer")
        if row["layer"] not in layers:
            fail("density references unknown layer: " + str(row["layer"]))
        minimum = finite(row["min"], "density min", nonnegative=True)
        maximum = finite(row["max"], "density max", nonnegative=True)
        if minimum > maximum or maximum > 1:
            fail("density requires 0 <= min <= max <= 1")
        if "fill_spacing" in row:
            finite(row["fill_spacing"], "density fill_spacing", positive=True)
        if "fill_cell_ref" in row:
            string(row["fill_cell_ref"], "density fill_cell_ref")


def _dbu_units(value):
    database_unit = finite(value["database_unit"], "database_unit", positive=True)
    dbu_per_um = positive_int(value["dbu_per_um"], "dbu_per_um")
    if abs(dbu_per_um * database_unit - 1e-6) > 1e-18:
        fail("dbu_per_um disagrees with database_unit")
    if value["user_unit"] != "micron":
        fail("user_unit must be micron")
    for item in value["grid"]["step"] + value["grid"]["origin"]:
        quotient = item / database_unit
        if abs(quotient - round(quotient)) > 1e-9:
            fail("grid coordinates must align to database_unit")
    if value["grid"]["step"][0] < database_unit or value["grid"]["step"][1] < database_unit:
        fail("grid step must be at least one database unit")
    return database_unit


def validate_process(value):
    """Validate one package-level layout.json draft document."""
    envelope_fields(
        value,
        ("source", "depends_on", "unit", "database_unit", "dbu_per_um", "user_unit",
         "grid", "layers", "layer_groups", "vias", "routing", "stream", "pin_lpp_map"),
        ("evidence", "density"),
    )
    if value["unit"] != "m":
        fail("layout unit must be m")
    fields(value["grid"], ("step", "origin"))
    pair(value["grid"]["step"], "grid step", positive=True)
    pair(value["grid"]["origin"], "grid origin")
    database_unit = _dbu_units(value)
    layers = _layers(value["layers"])
    _routing(value["routing"], layers)
    _layer_groups(value["layer_groups"], layers, value["routing"]["layer_order"])
    _vias(value["vias"], layers)
    _via_stack(value["vias"], value["routing"]["layer_order"])
    _stream(value["stream"], database_unit)
    _pin_lpp_map(value["pin_lpp_map"], layers, value["layer_groups"])
    if "density" in value:
        _density(value["density"], layers)
    return value


__all__ = ["validate_process"]
