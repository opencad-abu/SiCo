"""Validate one device-level ``layout.json`` draft (Appendix E device profile)."""

import re

from .jsonio import fail
from .layout_common import (
    CELL_USAGE,
    ORIENTATIONS,
    RUNTIME_ONLY,
    VARIANTS,
    digest,
    enum,
    enum_list,
    envelope_fields,
    finite,
    nonnegative_int,
    pair,
    string,
)
from .layout_parameters import validate_parameters
from .layout_terminals import validate_constraints, validate_terminals
from .validate import fields

RANGE = re.compile(
    r"^(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?(?:m|um|nm)?"
    r"(?:\s*\.\.\s*(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?(?:m|um|nm)?)?\Z"
)


def _master(value):
    fields(value, ("library", "cell", "view"))
    for name in ("library", "cell", "view"):
        string(value[name], "master " + name)


def _pcell_views(value, pcells, available):
    if not isinstance(value, dict):
        fail("pcell_views must be a mapping")
    if set(value) != set(pcells):
        fail("pcell_views must name exactly the declared pcells")
    for views in value.values():
        if not isinstance(views, list) or not views or any(
                not isinstance(item, str) or not item for item in views):
            fail("pcell views must be non-empty string arrays")
        if len(views) != len(set(views)):
            fail("pcell views must be unique")
    if available:
        for name, views in value.items():
            if "layout" not in views:
                fail("layout-capable pcell must expose a layout view: " + name)


def _qualification(value, master, parameter_names):
    fields(value, ("drc", "lvs", "parameters", "scope", "evidence_digest"),
           ("authorization_ref",))
    string(value["drc"], "qualification drc")
    string(value["lvs"], "qualification lvs")
    digest(value["evidence_digest"], "qualification evidence_digest")
    params = value["parameters"]
    if not isinstance(params, dict) or not params:
        fail("qualification parameters must be a non-empty mapping")
    for name, item in params.items():
        string(name, "qualification parameter name")
        if isinstance(item, list):
            if not item or any(not isinstance(part, str) or not part for part in item):
                fail("qualification parameter arrays must contain strings")
        elif not isinstance(item, str) or not item:
            fail("qualification parameter values must be strings or string arrays")
    for name in ("l", "w_range", "fingers_range"):
        if name in params and not RANGE.fullmatch(params[name]):
            fail("qualification " + name + " must be a number/range with optional m/um/nm")
    if "bodytie_type" in params and not isinstance(params["bodytie_type"], list):
        fail("qualification bodytie_type must be an array")
    if "bodytie_type" in parameter_names and "bodytie_type" not in params:
        fail("qualification must scope bodytie_type")
    scope = value["scope"]
    if isinstance(scope, str):
        scope = [item.strip() for item in scope.split(",") if item.strip()]
    if not isinstance(scope, list) or not scope or any(
            not isinstance(item, str) or item.count("/") != 1
            or not all(part for part in item.split("/")) for item in scope):
        fail("qualification scope must contain library/cell references")
    if len(scope) != len(set(scope)):
        fail("qualification scope must be unique")
    current = master["library"] + "/" + master["cell"]
    if current not in scope:
        fail("qualification scope must include the current master")
    if "authorization_ref" in value:
        string(value["authorization_ref"], "qualification authorization_ref")


def _placement_policy(value):
    if value is None:
        return
    for key in RUNTIME_ONLY.intersection(value):
        fail("Runtime geometry is not allowed in placement_policy: " + key)
    fields(value, (), ("orientations", "gate_axis", "pin_access_sides", "mirror_axes"))
    if "orientations" in value:
        enum_list(value["orientations"], ORIENTATIONS, "placement orientation")
    if "gate_axis" in value:
        enum(value["gate_axis"], frozenset({"horizontal", "vertical"}), "gate_axis")
    if "pin_access_sides" in value:
        enum_list(value["pin_access_sides"], frozenset({"left", "right", "top", "bottom"}),
                  "pin access side")
    if "mirror_axes" in value:
        enum_list(value["mirror_axes"], frozenset({"x", "y"}), "mirror axis")


def _hierarchy(value):
    if value is None:
        return
    fields(value, ("mode",), ("children",))
    enum(value["mode"], frozenset({"fixed", "dynamic"}), "hierarchy mode")
    if value["mode"] == "dynamic":
        if "children" in value:
            fail("dynamic hierarchy cannot contain children")
        return
    if not isinstance(value.get("children"), list) or not value["children"]:
        fail("fixed hierarchy requires a non-empty children array")
    for row in value["children"]:
        fields(row, ("name", "master", "gds_structure", "usage", "origin",
                     "orientation", "array", "terminal_map"))
        string(row["name"], "child name")
        _master(row["master"])
        string(row["gds_structure"], "child gds_structure")
        enum(row["usage"], CELL_USAGE, "child usage")
        pair(row["origin"], "child origin")
        enum(row["orientation"], ORIENTATIONS, "child orientation")
        if (not isinstance(row["array"], list) or len(row["array"]) != 2
                or any(type(item) is not int or item <= 0 for item in row["array"])):
            fail("child array must be two positive integers")
        if not isinstance(row["terminal_map"], dict) or any(
                not isinstance(k, str) or not isinstance(v, str)
                for k, v in row["terminal_map"].items()):
            fail("child terminal_map must map strings to strings")


def _guard_ring(value, layers=None):
    if value is None:
        return
    if not isinstance(value, dict) or "supported" not in value:
        fail("guard_ring requires supported")
    if type(value["supported"]) is not bool:
        fail("guard_ring supported must be boolean")
    if value["supported"]:
        fields(value, ("supported", "margin", "layer"))
        finite(value["margin"], "guard_ring margin", positive=True)
        string(value["layer"], "guard_ring layer")
        if layers is not None and value["layer"] not in layers:
            fail("guard_ring references unknown layer: " + value["layer"])
    else:
        fields(value, ("supported", "reason"))
        string(value["reason"], "guard_ring reason")


def _finger_semantics(value):
    if value is None:
        return
    fields(value, ("topology", "source_pin_count", "drain_pin_count", "gate_pin_count"))
    enum(value["topology"], frozenset({"S-D-S", "D-S-D", "unknown"}), "finger topology")
    for key in ("source_pin_count", "drain_pin_count", "gate_pin_count"):
        nonnegative_int(value[key], "finger " + key)


def validate_device(value, *, process=None, cdf_parameters=None, term_order=None):
    """Validate one device-level ``layout.json`` draft document."""
    if not isinstance(value, dict):
        fail("layout document must be an object")
    for key in RUNTIME_ONLY.intersection(value):
        fail("Runtime geometry is not allowed in layout.json: " + key)
    envelope_fields(
        value,
        ("source", "depends_on", "unit", "master", "pcells", "variant",
         "layout_available", "layout_unavailable_reason"),
        ("evidence", "pcell_views", "parameters", "terminals", "constraints",
         "x_layout_ready", "x_layout_qualification", "placement_policy",
         "finger_semantics", "gds_structure", "hierarchy", "guard_ring"),
    )
    if value["unit"] != "m":
        fail("layout unit must be m")
    _master(value["master"])
    available = value["layout_available"]
    if type(available) is not bool:
        fail("layout_available must be boolean")
    reason = value["layout_unavailable_reason"]
    if available:
        if reason is not None:
            fail("available layout cannot have layout_unavailable_reason")
        if value["master"]["view"] != "layout":
            fail("available layout master.view must be layout")
    else:
        string(reason, "layout_unavailable_reason")
    enum(value["variant"], VARIANTS, "layout variant")
    pcells = value["pcells"]
    if not isinstance(pcells, list) or any(
            not isinstance(item, str) or not item for item in pcells):
        fail("pcells must be an array of strings")
    if len(pcells) != len(set(pcells)):
        fail("pcells must not contain duplicates")
    if available and not pcells:
        fail("layout_available=true requires at least one pcell")
    if pcells:
        if "pcell_views" not in value:
            fail("pcells require pcell_views")
        _pcell_views(value["pcell_views"], pcells, available)
    if "x_layout_ready" in value:
        if type(value["x_layout_ready"]) is not bool:
            fail("x_layout_ready must be boolean")
        if value["x_layout_ready"] != available:
            fail("x_layout_ready must equal layout_available")
    if available:
        for key in ("parameters", "terminals", "x_layout_qualification"):
            if key not in value:
                fail("layout_available=true requires " + key)
    if process is not None:
        from .layout_process import validate_process
        validate_process(process)
    effect_terminals = (
        validate_parameters(value["parameters"], cdf_parameters)
        if "parameters" in value else set()
    )
    if "terminals" in value:
        terminal_names, terminal_ties = validate_terminals(value["terminals"], process, term_order)
    else:
        terminal_names, terminal_ties = set(), set()
    unknown_effects = effect_terminals - terminal_names
    if unknown_effects:
        fail("layout effects reference unknown terminals: " + ", ".join(sorted(unknown_effects)))
    required_ties = terminal_ties | effect_terminals
    validate_constraints(value.get("constraints"), required_ties, terminal_names)
    if "x_layout_qualification" in value:
        _qualification(value["x_layout_qualification"], value["master"],
                       set(value.get("parameters", {})))
    _placement_policy(value.get("placement_policy"))
    _finger_semantics(value.get("finger_semantics"))
    _hierarchy(value.get("hierarchy"))
    _guard_ring(value.get("guard_ring"), process.get("layers") if process is not None else None)
    if "gds_structure" in value:
        string(value["gds_structure"], "gds_structure")
    return value


__all__ = ["validate_device"]
