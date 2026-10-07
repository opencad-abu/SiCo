"""Current collector electrical rows must contain observed, mutually consistent facts."""

from .template_schema import TemplateError

# Missing is not equivalent to an observed null, false, or scalar width.
_REQUIRED = {
    "instance": {"master_available", "schematic_available", "terminal_names", "master_view_type"},
    "instance_terminal": {"net"},
    "net": {"numBits", "sigType", "is_global", "instance_terminal_count"},
    "terminal": {"direction", "numBits", "net"},
    "pin": {"net"},
}


def validate_electrical_capture(rows):
    instances = {r["name"]: r for r in rows if r["kind"] == "instance"}
    ports = {r["name"]: r for r in rows if r["kind"] == "terminal"}
    nets = {r["name"]: r for r in rows if r["kind"] == "net"}
    for row in rows:
        kind = row["kind"]
        missing = _REQUIRED.get(kind, set()) - row.keys()
        if missing:
            raise TemplateError("incomplete electrical observation: " + kind + " missing "
                                + ", ".join(sorted(missing)))
    for row in rows:
        kind = row["kind"]
        if kind in {"net", "terminal"} and type(row["numBits"]) is not int:
            raise TemplateError("electrical observation requires an observed integer width")
        if kind == "net" and type(row["instance_terminal_count"]) is not int:
            raise TemplateError("electrical observation requires an observed endpoint count")
        if kind == "instance_terminal":
            inventory = instances[row["instance"]]["terminal_names"]
            if inventory is not None and row["name"] not in inventory:
                raise TemplateError("saved endpoint is absent from master terminal inventory")
        if kind == "pin" and row["net"] != ports[row["terminal"]]["net"]:
            raise TemplateError("pin net differs from its owning terminal net")
        if kind == "terminal" and row["net"] is not None:
            if row["numBits"] != nets[row["net"]]["numBits"]:
                raise TemplateError("terminal width differs from its owning net width")
