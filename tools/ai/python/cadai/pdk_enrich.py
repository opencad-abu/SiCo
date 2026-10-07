"""Front-end enrichment of one collected device (tiers, ranges, categories, readiness).

The runtime capture owns observations; this module only *projects* them together with the
workspace file-layer index (``cadai.pdk_files``).  Every derived value keeps its evidence:
``source`` (call or file+line), ``method`` (observed/imported/derived) and explicit ``unknown``.
No PDK rule is evaluated and no deck content is interpreted beyond the declared names/keys.
"""

from __future__ import annotations

import re
from copy import deepcopy

from .pdk_files import candidate_terminal_map

ENRICHMENT_REVISION = "20260919.physical-inputs.v2"

# More specific functional categories first; used only to pick one "primary" label.
PRIMARY_CATEGORY_PREFERENCE = ("bipolars", "diodes", "parasitical_dio", "varactors", "mos",
                               "capacitor", "resistors", "poly", "diffusion", "nwell", "metal",
                               "primitives")
CATEGORY_DEFAULT_KIND = {"bipolars": "bjt", "diodes": "diode", "varactors": "varactor",
                         "capacitor": "capacitor", "resistors": "resistor",
                         "parasitical_dio": "parasitic"}
CATEGORY_EXPECTED_KINDS = {
    "mos": {"nmos", "pmos"}, "resistors": {"resistor"}, "capacitor": {"capacitor"},
    "diodes": {"diode"}, "bipolars": {"bjt"}, "varactors": {"varactor"},
    "parasitical_dio": {"parasitic", "diode"}, "poly": {"resistor"},
    "diffusion": {"resistor", "diode", "parasitic", "nmos", "pmos"},
    "metal": {"resistor", "capacitor", "parasitic"},
    "nwell": {"parasitic", "diode", "nmos", "pmos"}, "primitives": set(),
}
PARAM_LIMIT_ALIAS = {"fingers": "nf", "nf": "nf", "m": "mr", "mf": "mr", "seg": "seg"}
CORE_INTERFACE = frozenset({"model", "w", "l", "m", "nf", "fingers", "seg", "r", "c", "area", "pj"})
# Interface is discoverability, not proof that these fields are independent inputs.
# The calculated output depends on the live CDF mode and callback evidence.
MODE_GEOMETRY_INTERFACE = frozenset({"calculatedparam", "segw", "segl", "segments"})
DERIVED_NAME_RE = re.compile(r"(?i)^(sim[a-z0-9_]*|eff[a-z]|segr|tot[a-z]*"
                             r"|showotherparams|pasupdateparamlist)")


def _known(node):
    return isinstance(node, dict) and node.get("status") == "known"


def _value(node):
    if not _known(node):
        return None
    value = node.get("value")
    if node.get("type") == "list":
        return [_value(item) for item in value or []]
    return value


def simulator_interfaces(value):
    """Per-simulator instance interface names from the normalized simInfo items."""
    result = {}
    for item in (value.get("simulators") or {}).get("items", []):
        raw = item.get("raw") if isinstance(item, dict) else None
        names = []
        if isinstance(raw, dict):
            for key in ("instParameters", "otherParameters"):
                names.extend(name for name in (_value(raw.get(key)) or []) if isinstance(name, str))
        result[item.get("name")] = names
    return result


def parameter_tier(name, interface_names, deck_names):
    """Tier by sourced evidence first, naming rule second (never by guess)."""
    if (name in interface_names or name in deck_names or name in CORE_INTERFACE
            or (name or "").casefold() in MODE_GEOMETRY_INTERFACE):
        return "interface"
    if DERIVED_NAME_RE.match(name or ""):
        return "derived"
    return "auxiliary"


def _limit_block(limits, prefix):
    low, high = (limits or {}).get(prefix + "min"), (limits or {}).get(prefix + "max")
    if low is None and high is None:
        return None
    return {"status": "complete" if low and high else "partial",
            "min": (low or {}).get("value"), "max": (high or {}).get("value"),
            "unit": "m", "inclusive": True, "scope": "model_bin",
            "source": {"kind": "model_deck", "method": "imported",
                       "file": (low or high)["file"], "line": (low or high)["line"],
                       "as_entered": {"min": (low or {}).get("as_entered"),
                                      "max": (high or {}).get("as_entered")}}}


def _documented_block(documented, prefix):
    if not documented:
        return None
    row = (documented.get("limits") or {}).get(prefix)
    if not row:
        return None
    return {"status": "partial", "min": row["min"], "max": None, "unit": "m", "inclusive": True,
            "scope": "design_rule", "mode": "documented", "as_entered": row["as_entered"],
            "source": {"kind": "model_readme_table", "method": "imported",
                       "file": documented["file"], "line": documented["line"],
                       "headers": documented.get("headers"),
                       "note": "documented minimum from the model readme table; only a minimum is published"}}


def _merge(scopes):
    lows = [block["min"] for block in scopes if block and block.get("min") is not None]
    highs = [block["max"] for block in scopes if block and block.get("max") is not None]
    names = [block["scope"] for block in scopes if block]
    return {"min": max(lows) if lows else None, "max": min(highs) if highs else None,
            "effective_from": names,
            "status": "complete" if lows and highs and len(names) == 2 else "partial"}


def _parse_default(text):
    from .pdk_files import parse_si
    return parse_si(text)


def range_block(limits, documented, name):
    prefix = PARAM_LIMIT_ALIAS.get(name, name)
    model_block = _limit_block(limits, prefix)
    design_block = _documented_block(documented, prefix)
    if model_block is None and design_block is None:
        return {"status": "unknown", "min": None, "max": None,
                "source": {"plan": "model deck limits or PDK documentation capture"}}
    merged = _merge([model_block, design_block])
    block = {"status": merged["status"], "min": merged["min"], "max": merged["max"], "unit": "m",
             "inclusive": True, "scope": "effective", "effective_from": merged["effective_from"],
             "scopes": {"model_bin": model_block, "design_rule": design_block},
             "source": (model_block or design_block)["source"]}
    return block


def _default_in_range(default, block):
    if block.get("status") == "unknown" or not isinstance(default, str):
        return "unknown"
    value = _parse_default(default)
    if value is None:
        return "unknown"
    low, high = block.get("min"), block.get("max")
    if low is not None and value < low - 1e-18:
        return False
    if high is not None and value > high + 1e-18:
        return False
    return True


def _range_readiness(ranges, model_bin, documented):
    """Geometry ranges only; a device without W/L-style parameters is `not_applicable`."""
    geometry = [name for name in ("w", "l", "wf", "lf", "segw", "segl") if name in ranges]
    if not geometry:
        return "not_applicable"
    known = [name for name in geometry if ranges[name].get("status") != "unknown"
             and (ranges[name].get("min") is not None or ranges[name].get("max") is not None)]
    if len(known) == len(geometry):
        return "ok"
    return "partial" if (model_bin or documented) else "missing"


def port_crosscheck(ports, terminals):
    cdf = [port.get("name") for port in ports]
    return {"cdf": cdf, "deck": list(terminals or []),
            "same_set": bool(terminals) and {name.casefold() for name in cdf} == {t.casefold() for t in terminals},
            "same_order": bool(terminals) and [name.casefold() for name in cdf] == [t.casefold() for t in terminals],
            "count_match": bool(terminals) and len(terminals) == len(cdf),
            "candidate_map": candidate_terminal_map(cdf, terminals or []),
            "note": "deck terminals are the netlist order; a candidate map must be confirmed "
                    "against the PDK netlisting convention (CAD owner)"}


def _simulation_readiness(value, interfaces):
    if not interfaces:
        return "missing"
    for names in interfaces.values():
        if names:
            return "ok"
    return "partial"


def enrich(value, resolution, categories, index):
    """Return a deep-copied device value with categories, tiers, ranges, crosscheck and readiness."""
    result = deepcopy(value)
    blocks = result.get("classification") or {}
    registry = categories or {"primary": None, "all": [], "ambiguous": False,
                              "source": {"kind": "categories_unavailable", "method": "unavailable"}}
    result["categories"] = registry
    primary = registry.get("primary")
    expected = CATEGORY_EXPECTED_KINDS.get(primary, set())
    resolved_kind = CATEGORY_DEFAULT_KIND.get(primary)
    if expected and resolved_kind:
        kind = (blocks.get("kind") or "unknown") if isinstance(blocks, dict) else "unknown"
        if kind == "unknown" or kind not in expected:
            if kind != "unknown":
                blocks["conflict"] = {"name_rule_kind": kind, "category": primary,
                                      "resolved_by": "library_category",
                                      "resolved_kind": resolved_kind}
            blocks["kind"] = resolved_kind
            blocks["status"] = "derived_from_category"
            blocks["source"] = {"reason": "library category registry",
                                "inputs": {"category": primary}}
    interfaces = simulator_interfaces(result)
    interface_names = {name for names in interfaces.values() for name in names}
    deck_names = {name for name in resolution.get("instance_args", []) if isinstance(name, str)}
    parameters = (result.get("parameters") or {}).get("items", [])
    ranges, tiers = {}, {}
    for item in parameters:
        name = item.get("name")
        tier = parameter_tier(name, interface_names, deck_names)
        block = range_block(resolution.get("limits"), resolution.get("documented"), name)
        default = item.get("default")
        default_text = default.get("value") if isinstance(default, dict) else default
        item["tier"] = tier
        item["tier_reason"] = (["sim_info"] if name in interface_names else []) + \
                              (["model_deck"] if name in deck_names else []) + \
                              (["core_set"] if name in CORE_INTERFACE else []) + \
                              (["mode_or_geometry_name"] if
                               (name or "").casefold() in MODE_GEOMETRY_INTERFACE else [])
        item["range"] = block
        item["default_in_range"] = _default_in_range(default_text, block)
        tiers[name] = tier
        ranges[name] = block
    result["model_resolution"] = {key: resolution.get(key) for key in
                                  ("queried", "resolved", "status", "basis", "decks")}
    result["model_decks"] = resolution.get("decks", [])
    result["limits"] = {
        "model_deck": resolution.get("limits") or {},
        "documented_minima": resolution.get("documented"),
        "readme_rows": resolution.get("readme", []),
        "source_root": index.get("root"),
        "index_digest": index.get("digest"),
    }
    result["parameter_tiers"] = {"counts": {tier: sum(1 for value_ in tiers.values() if value_ == tier)
                                            for tier in ("interface", "derived", "auxiliary")},
                                 "interface": sorted(name for name, tier in tiers.items()
                                                     if tier == "interface")}
    result["port_crosscheck"] = port_crosscheck(result.get("ports", {}).get("items", []),
                                               resolution.get("terminals"))
    defaults_known = all(isinstance((item.get("default") or {}).get("status"), str)
                         and (item.get("default") or {}).get("status") == "known"
                         for item in parameters if isinstance(item.get("default"), dict))
    pins = (result.get("geometry") or {}).get("items", [])
    readiness = {
        "identity": "ok" if resolution.get("status") in {"found", "decks_unavailable",
                                                          "decks_truncated"} else "partial",
        "ports": "ok" if len(result.get("ports", {}).get("items", [])) >= 2 and
                         all(port.get("direction") for port in result["ports"]["items"]) else "partial",
        "pins": "ok" if any((figure.get("anchors") or {}).get("selected") or
                            (figure.get("anchors") or {}).get("candidates") for figure in pins)
                else "partial" if pins else "missing",
        "parameters": "ok" if defaults_known else "partial",
        "ranges": _range_readiness(ranges, resolution.get("limits") or {},
                                   resolution.get("documented")),
        "simulation": _simulation_readiness(result, interfaces),
        "docs": "indexed" if index.get("document_entries") else "missing",
    }
    readiness["overall"] = ("ready_for_design"
                            if all(readiness[key] in {"ok", "not_applicable"} for key in
                                   ("identity", "ports", "pins", "parameters", "ranges", "simulation"))
                            else "partial")
    result["readiness"] = readiness
    result["file_layer"] = {"available": index.get("available"),
                            "source_root": index.get("root"),
                            "index_digest": index.get("digest"),
                            "rule_decks": index.get("rule_decks", []),
                            "documents": index.get("documents", [])}
    return result


def primary_category(categories):
    for name in PRIMARY_CATEGORY_PREFERENCE:
        if name in categories:
            return name
    return sorted(categories)[0] if categories else None


def category_registry(capture_data, library):
    """Normalize the `categories` capture into a per-cell registry with provenance."""
    items = (capture_data or {}).get("items") or []
    cells, all_categories, children = {}, {}, {}
    for item in items:
        name = item.get("name")
        members = [member for member in item.get("members", []) if isinstance(member, str)]
        if not isinstance(name, str) or not name:
            continue
        all_categories[name] = sorted(members)
        sub = [row.get("name") for row in item.get("children") or []
               if isinstance(row, dict) and isinstance(row.get("name"), str)]
        if sub:
            children[name] = sorted(sub)
        for member in members:
            cells.setdefault(member, []).append(name)
    return {"cells": cells, "categories": all_categories, "children": children,
            "unassigned": sorted(x for x in ((capture_data or {}).get("unassigned") or [])
                                 if isinstance(x, str)),
            "status": (capture_data or {}).get("status", "unknown"),
            "issues": list((capture_data or {}).get("issues") or []),
            "source": {"kind": "ddCat_registry", "library": library,
                       "source": (capture_data or {}).get("source")}}


def device_categories(registry, cell, ddcat_cells=None):
    names = sorted((registry.get("cells") or {}).get(cell, []))
    primary = primary_category(names)
    if ddcat_cells is None:
        ddcat_cells = set(registry.get("ddcat_cells") or [])
    runtime = cell in ddcat_cells
    authority = registry.get("category_source") or {}
    return {"primary": primary, "all": names, "ambiguous": len(names) > 1,
            "source": {"kind": "ddCat_registry" if runtime else "library_category_files",
                       "method": "observed" if runtime else "imported",
                       "categories": names,
                       "category_authority": {name: authority.get(name, "unknown")
                                              for name in names},
                       "fallback": "library_category_files",
                       "registry_status": registry.get("status")}}


def file_category_registry(file_registry, library):
    """Fallback registry built from ``<library>/*.Cat`` (D1: second authority after ddCat*)."""
    cells = {cell: sorted(str(name) for name in names)
             for cell, names in (file_registry.get("cells") or {}).items()}
    categories = {name: sorted((entry.get("cells") or {}).keys())
                  for name, entry in (file_registry.get("categories") or {}).items()}
    children = {name: sorted(str(row.get("name")) for row in entry.get("children") or []
                             if isinstance(row, dict) and isinstance(row.get("name"), str))
                for name, entry in (file_registry.get("categories") or {}).items()}
    children = {name: values for name, values in children.items() if values}
    return {"cells": cells, "categories": categories, "children": children, "unassigned": [],
            "status": "complete" if cells else "unavailable", "issues": [],
            "source": {"kind": "library_category_files", "library": library,
                       "files": [row.get("file") for row in (file_registry.get("files") or [])]}}


def category_evidence(registry, file_registry):
    """Bounded ddCat-vs-.Cat cross-check evidence; the runtime registry stays authoritative."""
    registry_cells = set(registry.get("cells") or {})
    file_cells = set(file_registry.get("cells") or {})
    return {"registry_cells": len(registry_cells), "file_cells": len(file_cells),
            "registry_only": sorted(registry_cells - file_cells)[:64],
            "file_only": sorted(file_cells - registry_cells)[:64],
            "consistent": bool(registry_cells) and registry_cells == file_cells}


def effective_categories(session_data, library, file_registry):
    """Category registry for one library: ddCat* wins per cell, ``<lib>/*.Cat`` fills the rest.

    D1 keeps the runtime registry authoritative for every category name it exposes (its
    member lists are the truth, including cells it dropped because they no longer exist);
    the file registry supplies the category names the runtime registry does not expose
    (here: diffusion/metal/nwell/poly). Cells therefore keep the union of both authorities
    and every device stays categorized. The difference stays as cross-check evidence.
    """
    registry = category_registry(session_data, library)
    evidence = category_evidence(registry, file_registry)
    files = file_category_registry(file_registry, library)
    runtime = registry.get("categories") or {}
    file_only = files.get("categories") or {}
    categories, authority = {}, {}
    for name in sorted(set(runtime) | set(file_only)):
        if name in runtime:
            categories[name] = list(runtime[name])
            authority[name] = "ddCat_registry"
        else:
            categories[name] = list(file_only[name])
            authority[name] = "library_category_files"
    cells = {}
    for name, members in categories.items():
        for member in members:
            cells.setdefault(member, []).append(name)
    ddcat = {cell: names for cell, names in cells.items()
             if any(authority[name] == "ddCat_registry" for name in names)}
    children = {**(files.get("children") or {}), **(registry.get("children") or {})}
    if ddcat:
        status = "complete" if registry.get("status") == "complete" else "partial"
    else:
        status = files.get("status", "unknown") if cells else "unavailable"
    # The runtime registry keeps its own status; uncovered categories are reported through
    # `file_fallback_cells` and the cross-check instead of inventing a partial status.
    issues = list(registry.get("issues") or [])
    result = {"cells": {cell: sorted(names) for cell, names in cells.items()},
              "categories": categories, "category_source": authority, "children": children,
              "unassigned": list(registry.get("unassigned") or []),
              "ddcat_cells": sorted(ddcat),
              "file_fallback_cells": sorted(set(cells) - set(ddcat))[:512],
              "status": status, "issues": issues,
              "source": registry.get("source") if ddcat else files.get("source"),
              "cross_check": evidence}
    if not ddcat and cells:
        result["fallback"] = "runtime_registry_empty"
    return result


def qualification(resolution):
    """D4 device inclusion: a library cell qualifies when its model is declared in a deck."""
    status = resolution.get("status")
    if status == "found":
        return True, None
    if status == "decks_unavailable":
        return True, "model_decks_unavailable"
    if status == "decks_truncated":
        # The deck index was cut short: absence is not provable, so keep the device.
        return True, "model_decks_truncated"
    return False, "model_not_in_decks"
