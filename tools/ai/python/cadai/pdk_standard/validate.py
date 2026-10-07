"""Structural checks for standard core files and confirmed CDF rules."""

import re

from . import SUPPORTED_VERSIONS
from .constraints import accepts, condition, domain, grid, numeric, unknown, feasible
from .jsonio import fail

USES = {"circuit", "testbench", "extraction", "verification"}
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


def fields(row, required, optional=()):
    if not isinstance(row, dict) or set(required) - set(row) or any(
            k not in set(required) | set(optional) and not k.startswith("x_") for k in row):
        fail("Missing or unsupported fields: " + ", ".join(required))


def identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        fail("Invalid standard ID")


def envelope(value):
    if not isinstance(value.get("source"), str) or not isinstance(value.get("depends_on"), list):
        fail("Content requires source and depends_on")
    if not isinstance(value.get("evidence", {}), dict):
        fail("Invalid evidence map")


def cdf(value):
    fields(value, ("source", "depends_on", "presence", "parameters", "execution"),
           ("evidence", "rules", "interface_parameters"))
    envelope(value)
    if value["presence"] not in {"present", "absent", "unknown"}:
        fail("Invalid CDF presence")
    params = value["parameters"]
    if not isinstance(params, dict):
        fail("CDF parameters must be a mapping")
    if "interface_parameters" in value:
        selected = value["interface_parameters"]
        if (not isinstance(selected, list) or any(not isinstance(p, str) for p in selected)
                or len(selected) != len(set(selected)) or set(selected) - set(params)):
            fail("CDF interface parameters must name unique captured parameters")
    context = {"definitions": params, "values": {n: q["default"] for n, q in params.items()
                                                if not unknown(q["default"])}}
    for name, p in params.items():
        fields(p, ("type", "unit", "meaning", "write", "requirement", "default", "domain"),
               ("write_when", "required_when", "grid", "step", "suggested", "depends_on"))
        if not isinstance(name, str) or not name or len(name) > 256:
            fail("Invalid CDF parameter name")
        typ = p["type"]
        if not unknown(p["meaning"]) and (not isinstance(p["meaning"], str) or not 1 <= len(p["meaning"]) <= 256):
            fail("Parameter meaning must be a short string")
        if not unknown(typ) and (not isinstance(typ, str) or typ not in {"number", "integer", "string", "boolean"}):
            fail("Invalid parameter type")
        if not unknown(p["unit"]) and (not isinstance(p["unit"], str) or not p["unit"]):
            fail("Parameter unit is required")
        if p["write"] not in {"allow", "deny", "conditional", "unknown"}:
            fail("Invalid writable policy")
        if p["requirement"] not in {"explicit", "default", "conditional", "not_applicable", "unknown"}:
            fail("Invalid assignment policy")
        if p["write"] == "deny" and p["requirement"] == "explicit":
            fail("Read-only parameter cannot require explicit assignment")
        if p["write"] in {"allow", "conditional"} and p["requirement"] == "not_applicable":
            fail("Writable parameter requires an assignment policy")
        for key, policy in (("write_when", "write"), ("required_when", "requirement")):
            if p[policy] == "conditional":
                condition(p.get(key), params)
            elif key in p:
                fail("Condition without conditional policy")
        domain(p["domain"], typ if isinstance(typ, str) else "unknown", params)
        if "grid" in p:
            grid(p["grid"])
            if typ not in ("number", "integer"):
                fail("Grid only applies to numeric parameters")
        feasible(p)
        if "step" in p:
            if typ not in ("number", "integer"):
                fail("Recommended step requires numeric parameter")
            fields(p["step"], ("mode", "value"))
            mode, step = p["step"]["mode"], p["step"]["value"]
            if mode not in {"linear", "ratio"} or not numeric(step) or step <= (1 if mode == "ratio" else 0):
                fail("Invalid recommended step")
        for key in ("default", "suggested"):
            if key in p and not unknown(p[key]) and isinstance(typ, str):
                if accepts(p[key], p, context) is False:
                    fail("CDF " + key + " violates constraints: " + name)
        if set(p.get("depends_on", [])) - set(params):
            fail("Missing parameter dependency")
    fields(value["execution"], ("mode",), ("order", "implementation_ref"))
    mode = value["execution"]["mode"]
    if mode not in {"literal", "cdf", "unknown"}:
        fail("Unsupported CDF execution")
    if mode == "cdf":
        order = value["execution"].get("order", [])
        expected = {n for n, p in params.items() if p["write"] in {"allow", "conditional"}}
        if not isinstance(order, list) or len(order) != len(set(order)) or set(order) != expected:
            fail("Callback order must cover every writable parameter once")
    elif "order" in value["execution"]:
        fail("Order only applies to CDF execution")
    if value["presence"] == "absent" and (params or mode != "literal"):
        fail("Absent CDF requires empty parameters and literal execution")
    for rule in value.get("rules", {}).values():
        fields(rule, ("check", "message"), ("when",))
        condition(rule["check"], params)
        if "when" in rule:
            condition(rule["when"], params)
    visited = set()
    def visit(name, active):
        if name in visited:
            return
        if name in active:
            fail("Cyclic parameter dependency")
        for child in params[name].get("depends_on", []):
            visit(child, active | {name})
        visited.add(name)
    for name in params:
        visit(name, set())


def device(value):
    envelope(value)
    fields(value, ("source", "depends_on", "items"), ("evidence",))
    for key, row in value["items"].items():
        identifier(key)
        fields(row, ("library", "cell", "view", "categories", "voltage", "use"),
               ("dir", "reason", "limits", "recommended", "note", "aliases"))
        identifier(row["library"])
        if not isinstance(row["cell"], str) or not row["cell"]:
            fail("Actual cell name required")
        if not isinstance(row["use"], dict) or set(row["use"]) != USES or any(
                v not in {"allow", "deny", "unknown"} for v in row["use"].values()):
            fail("Every device usage needs an explicit state")
        if set(row["use"].values()) - {"allow"} and not row.get("reason"):
            fail("Denied/unknown device usage requires a reason")
        for rec in row.get("recommended", []):
            fields(rec, ("use", "for", "priority", "reason"))
            if row["use"].get(rec["use"]) != "allow" or type(rec["priority"]) is not int or rec["priority"] < 0:
                fail("Only allowed devices can be recommended")


def _document(name, value):
    def check(node):
        if node is None:
            fail("Null is not a standard fact value")
        if isinstance(node, dict):
            from .validate_package import state
            state(node)
            for child in node.values():
                check(child)
        elif isinstance(node, list):
            for child in node:
                check(child)
        elif type(node) is float and not numeric(node):
            fail("Non-finite fact value")
    check(value)
    if name == "model.json" or name.endswith("/simulation.json") or name.endswith("/properties.json"):
        from . import validate_optional
        return getattr(validate_optional, name.rsplit("/", 1)[-1][:-5])(value)
    if name.endswith("/iv.json"):
        fail("IV block validation is not yet supported by this reader", "unsupported_pdk_standard")
    if name.endswith("/cdf.json"):
        return cdf(value)
    if name.endswith("/symbol.json"):
        from .geometry import validate as symbol
        return symbol(value)
    if name == "device.json":
        from .validate_package import devices
        device(value)
        return devices(value)
    if name in {"category.json", "file.json"}:
        from .validate_package import catalog
        return catalog(name, value)
    if name == "package.json":
        fields(value, ("format", "schema_version", "package_id", "pdk_version", "options", "revision",
                       "libraries", "dependencies", "files", "created_at"),
               ("required_features", "publication"))
        if value["format"] != "sico.pdk.package" or value["schema_version"] not in SUPPORTED_VERSIONS:
            fail("Unsupported standard version", "unsupported_pdk_standard")
        identifier(value["package_id"])
        from .validate_package import manifest
        manifest(value)
        from .publication import validate_manifest
        validate_manifest(value)
    elif name == "sources.json":
        for key, row in value["items"].items():
            identifier(key)
            required = {"session": ("collector", "target"), "document": ("file_ref", "locator"),
                        "user": ("confirmed_by", "scope_ref", "scope_digest"),
                        "simulation": ("run_ref", "simulator"), "derived": ("inputs", "method")}
            if row.get("kind") not in required:
                fail("Unsupported source kind")
            fields(row, ("kind", "at", "summary", *required[row["kind"]]), ("evidence_ref",))
    else:
        fail("Unsupported logical standard document: " + name, "unsupported_pdk_standard")


def document(name, value):
    try:
        return _document(name, value)
    except (KeyError, TypeError, ValueError, AttributeError, RecursionError) as exc:
        fail("Invalid standard structure in " + name + ": " + str(exc))
