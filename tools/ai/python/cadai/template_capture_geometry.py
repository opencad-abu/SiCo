"""Validate capture v2 source/master evidence, before normalizing wire attachments."""

from .circuit_geometry_schema import BOX, POINT
from .circuit_spec_schema import CircuitSpecError, array, unique, validate
from .template_classification import SEMANTICS, graphic_role
from .template_schema import NAME, TemplateError
from .template_wire_contacts import NET_SEMANTICS

RECORD_FIELDS_V2 = {
    "instance_geometry": "instance library_path observation",
    "port_anchor": "terminal anchor_id xy",
    "dependency_guard": "before after",
}


def _observation(row, inventory):
    observation = row["observation"]
    if not isinstance(observation, dict) or type(observation.get("ok")) is not bool:
        raise TemplateError("invalid master geometry observation")
    if not observation["ok"]:
        if set(observation) - {"ok", "code", "message", "context_ref"}:
            raise TemplateError("invalid failed geometry observation")
        for key in ("code", "message"):
            validate(observation.get(key), {"type": "string", "minLength": 1, "maxLength": 4096})
        return
    required = {"ok", "occupied_bbox", "pin_centers", "signature"}
    if not required <= set(observation) or set(observation) - required - {"annotation_bbox"}:
        raise TemplateError("invalid geometry observation fields")
    if "annotation_bbox" in observation:
        validate(observation["annotation_bbox"], BOX)
    validate(observation["occupied_bbox"], BOX)
    box = observation["occupied_bbox"]
    if any(box[0][i] >= box[1][i] for i in (0, 1)):
        raise TemplateError("empty master occupied bbox")
    validate(observation["signature"], {"type": "string", "minLength": 1, "maxLength": 48000})
    centers = observation["pin_centers"]
    if not isinstance(centers, list) or not 1 <= len(centers) <= 64:
        raise TemplateError("invalid master pin inventory")
    found = set()
    for item in centers:
        if not isinstance(item, list) or len(item) != 2:
            raise TemplateError("invalid pin center row")
        terminal, points = item
        validate(terminal, NAME)
        validate(points, array(POINT, 8, 1))
        if terminal in found or len({tuple(p) for p in points}) != len(points):
            raise TemplateError("duplicate master terminal/anchor")
        if any(not all(box[0][k] <= p[k] <= box[1][k] for k in (0, 1)) for p in points):
            raise TemplateError("pin outside occupied bbox")
        found.add(terminal)
    if found != set(inventory or []):
        raise TemplateError("geometry/master terminal inventory differs")


def validate_geometry_capture(header, rows):
    try:
        if ("wire_net_semantics" in header
                and header["wire_net_semantics"] != NET_SEMANTICS):
            raise TemplateError("unsupported source wire net semantics")
        guards = [r for r in rows if r["kind"] == "dependency_guard"]
        if len(guards) != 1 or not isinstance(guards[0].get("before"), str):
            raise TemplateError("capture v2 requires source/master dependency evidence")
        if not guards[0]["before"] or guards[0]["before"] != guards[0].get("after"):
            raise TemplateError("source/master changed during capture")
        if len(guards[0]["before"].encode()) > 2 * 1024 * 1024:
            raise TemplateError("dependency evidence exceeds 2 MiB")
        instances = {r["name"]: r for r in rows if r["kind"] == "instance"}
        observations = unique(
            [r for r in rows if r["kind"] == "instance_geometry"], "instance", "captured geometry"
        )
        for name, row in observations.items():
            if name not in instances:
                raise TemplateError("geometry references absent instance")
            validate(row["library_path"], {"type": "string", "minLength": 1, "maxLength": 4096})
            _observation(row, instances[name].get("terminal_names"))
        expected = {
            name for name, inst in instances.items()
            if not (graphic_role(inst) if header.get("classification_semantics") == SEMANTICS
                    else inst["libName"] == "basic" and inst["cellName"] in
                    {"ipin", "opin", "iopin", "noConn", "gnd", "vdd"})
        }
        if header["asset"] == "schematic" and set(observations) != expected:
            raise TemplateError("capture v2 requires every instance geometry observation")
        ports = {r["name"] for r in rows if r["kind"] == "terminal"}
        anchors = set()
        for row in (r for r in rows if r["kind"] == "port_anchor"):
            validate(row["anchor_id"], NAME)
            validate(row["xy"], POINT)
            key = (row["terminal"], row["anchor_id"])
            if row["terminal"] not in ports or key in anchors:
                raise TemplateError("missing/duplicate port anchor")
            anchors.add(key)
        if header.get("asset") == "schematic" and not header.get("dbu_per_uu"):
            raise TemplateError("capture v2 requires source DBU scale")
    except (CircuitSpecError, KeyError, TypeError, ValueError) as exc:
        raise TemplateError("invalid capture geometry: " + str(exc)) from exc
