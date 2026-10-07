"""Sanitized publication projections of one stored template record.

Levels (per the schematic reuse plan):

- ``L1`` abstract: device classes, interface ports and derived relations/style;
  no masters, parameters, source names, source paths or raw coordinates.
- ``L2`` PDK identity: L1 plus PDK master identity and full relative schematic
  geometry (wires, junctions, pin geometry). Parameters stay out.
- ``L3`` sized: L2 plus the canonical physical parameters (W/L/NF/M/SEGW/SEGL
  family) that were observed on the source instances.

The raw capture record stays private evidence. Projections never contain
``value_skill`` payloads, instance source names or absolute library paths; the
original reference stays reachable through ``provenance.base_template_ref`` and
``capture_sha256``.
"""

from __future__ import annotations

import copy
import json

from .template_relations_legacy import relations
from .template_routing_style import routing_style
from .template_schema import RULE_VERSION, SCHEMA, SCHEMA_V3, TemplateError, canonical, digest

LEVELS = ("L1", "L2", "L3")
# L3 physical aliases changed; retain the existing raw/L1/L2 reference rules.
L3_RULE_VERSION = "20260919.physical-projection.v1"
SOURCE_DROP_KEYS = ("library_path", "working_directory", "forced_cds_lib", "cds_include_chain")
PROVENANCE_KEEP = (
    "origin",
    "author",
    "version",
    "description",
    "captured_on",
    "capture_tool",
    "capture_scope",
    "design_library",
    "note",
)
MAX_PARAMETERS_PER_DEVICE = 16

# Canonical physical parameter names per abstract device class. Only names that
# are already physical inputs are projected. Target-specific mode selectors
# (calculatedParam), effective lengths and parasitics are never copied.
PARAMETER_ALIASES = {
    "w": ("w",),
    "l": ("l",),
    "nf": ("fingers", "nf"),
    "m": ("m",),
    # Some PDKs expose these as camel-case CDF names (segW/segL), while the
    # canonical projection keeps lower-case keys. Preserve both spellings so a
    # sized resistor template does not silently fall back to segments only.
    "segw": ("segW", "segw"),
    "segl": ("segL", "segl"),
    "segments": ("segments",),
}
PARAMETERS_BY_CLASS = {
    "nmos": ("w", "l", "nf", "m"),
    "pmos": ("w", "l", "nf", "m"),
    "moscap_n": ("w", "l", "nf", "m"),
    "moscap_p": ("w", "l", "nf", "m"),
    "resistor": ("segw", "segl", "segments"),
    "capacitor": ("w", "l", "segments"),
    "diode": ("w", "l", "nf", "m"),
    "pnp": ("w", "l", "nf", "m"),
}


def _decoded(value):
    """``value_skill`` payloads are raw SKILL text; JSON string literals decode."""
    if not isinstance(value, str):
        return value
    text = value.strip()
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        try:
            return json.loads(text)
        except ValueError:
            return text[1:-1]
    return text


def _parameters(record):
    """Canonical observed parameters per device, plus explicit gaps."""
    kinds = {
        device["id"]: device.get("kind", "unknown")
        for device in record.get("topology", {}).get("devices", [])
    }
    identity = {}
    for row in record.get("assets", {}).get("schematic", {}).get("instances", []):
        if row.get("device") and row.get("source_name"):
            identity[row["source_name"]] = row["device"]
    observed = {}
    for row in record.get("assets", {}).get("schematic", {}).get("properties", []):
        device, name = row.get("instance"), row.get("name")
        if not device or not name:
            continue
        device = identity.get(device, device)
        observed.setdefault(device, {})[name] = row.get("value_skill", row.get("value"))
    devices, gaps = [], []
    for device in sorted(observed):
        kind = kinds.get(device, "unknown")
        allowed = PARAMETERS_BY_CLASS.get(kind)
        if allowed is None:
            if observed[device]:
                gaps.append({"code": "parameter_class_unknown", "device": device})
            continue
        values = observed[device]
        canonical_parameters = {}
        for target in allowed:
            for alias in PARAMETER_ALIASES[target]:
                if alias in values and values[alias] is not None:
                    canonical_parameters[target] = _decoded(values[alias])
                    break
        if not canonical_parameters:
            gaps.append({"code": "physical_parameters_missing", "device": device})
            continue
        devices.append(
            {
                "device": device,
                "kind": kind,
                "parameters": dict(list(canonical_parameters.items())[:MAX_PARAMETERS_PER_DEVICE]),
            }
        )
    return devices, gaps


def _net_map(record):
    """Interface nets keep their names; internal nets become ``n<i>`` placeholders."""
    names = {}
    topology = record.get("topology", {})
    boundary = {port["net"] for port in topology.get("ports", [])}
    for index, net in enumerate(topology.get("nets", [])):
        net_id = net["id"]
        if net_id in boundary:
            names[net_id] = net.get("source_name", net_id)
        else:
            names[net_id] = "n" + str(index)
    return names


def _anonymize_topology(record, net_names, keep_master):
    """Drop real instance names and internal net names; masters only in L2/L3."""
    topology = copy.deepcopy(record.get("topology", {}))
    for device in topology.get("devices", []):
        device.pop("source_name", None)
        if not keep_master:
            device.pop("master", None)
        device.pop("attributes", None)
        device.pop("observed_property_names", None)
        for pin in device.get("pins", []):
            if pin.get("net") in net_names:
                pin["net"] = net_names[pin["net"]]
    for net in topology.get("nets", []):
        net["source_name"] = net_names.get(net["id"], net["id"])
        net["is_global"] = bool(net.get("is_global"))
    for port in topology.get("ports", []):
        if port.get("net") in net_names:
            port["net"] = net_names[port["net"]]
    return topology


def _anonymize_derived(view, net_names, level):
    """Replace source names with stable ids; L1 keeps relations, not geometry."""
    anonymous = copy.deepcopy(view)
    if level == "L1":
        anonymous.pop("device_axes", None)  # Contains source coordinates, not abstract relations.
    name_to_device = {}
    for row in anonymous.get("instances", []):
        name_to_device[row.get("name")] = row.get("device")
        row["name"] = row.get("device", row.get("name"))
        if level == "L1":
            row.pop("xy", None)
            row.pop("bbox", None)

    def rename(name):
        return name_to_device.get(name, name)

    for pair in anonymous.get("mirrored_pairs", []):
        pair["a"], pair["b"] = rename(pair["a"]), rename(pair["b"])
        pair["shared_nets"] = sorted(
            net_names.get(net, net) for net in pair.get("shared_nets", [])
        )
    for edge in anonymous.get("alignment", []):
        edge["a"], edge["b"] = rename(edge["a"]), rename(edge["b"])
    for group in anonymous.get("groups", []):
        group["members"] = [rename(member) for member in group.get("members", [])]
    for key in ("rows", "columns"):
        for entry in anonymous.get(key, []):
            entry["members"] = [rename(member) for member in entry.get("members", [])]
    spacing = anonymous.get("spacing") or {}
    if isinstance(spacing.get("nearest"), dict):
        spacing["nearest"] = {
            rename(name): value for name, value in spacing["nearest"].items()
        }
    classes = spacing.get("classes")
    if isinstance(classes, dict) and isinstance(classes.get("members"), dict):
        classes["members"] = {
            key: [rename(member) for member in members]
            for key, members in classes["members"].items()
        }
    for net in anonymous.get("nets", []):
        net["net"] = net_names.get(net["net"], net["net"])
        if level == "L1":
            net["row_count"] = len(net.pop("rows", []))
            net["column_count"] = len(net.pop("columns", []))
    return anonymous


def _strip_source(source):
    kept = {
        key: value
        for key, value in (source or {}).items()
        if key not in SOURCE_DROP_KEYS
    }
    return kept


def _strip_provenance(provenance):
    """Keep short provenance facts; never republish paths or file inventories."""
    if not isinstance(provenance, dict):
        return {}
    kept = {}
    for key in PROVENANCE_KEEP:
        value = provenance.get(key)
        if not isinstance(value, (str, int, float, bool)):
            continue
        if "/" in str(value) or "\\" in str(value):
            continue
        kept[key] = value
    return kept


def _strip_asset(asset):
    """Remove instance/property payloads while keeping geometry and interfaces."""
    cleaned = copy.deepcopy(asset)
    cleaned.pop("wire_reference", None)
    cleaned.pop("wire_coverage", None)
    cleaned.pop("properties", None)
    cleaned.pop("instance_properties", None)
    cleaned.pop("multiplicity_properties", None)
    if isinstance(cleaned.get("source"), dict):
        cleaned["source"] = _strip_source(cleaned["source"])
    for instance in cleaned.get("instances", []):
        instance.pop("source_name", None)
    return cleaned


def project_record(record, level):
    """Return the sanitized projection of one stored record at ``level``."""
    if record.get("schema_version") == SCHEMA_V3:
        raise TemplateError("v3 projection requires reuse-contract migration support")
    if level not in LEVELS:
        raise TemplateError("unsupported detail level: " + str(level))
    base_ref = record.get("template_ref")
    if not base_ref:
        raise TemplateError("projection requires a stored template reference")
    net_names = _net_map(record)
    derived = {
        "relations": _anonymize_derived(relations(record), net_names, level),
        "routing_style": _anonymize_derived(routing_style(record), net_names, level),
    }
    result = {
        "schema_version": SCHEMA,
        "rule_version": L3_RULE_VERSION if level == "L3" else RULE_VERSION,
        "coordinate_space": record.get("coordinate_space"),
        "detail_level": level,
        "source": _strip_source(record.get("source")),
        "capture_sha256": record.get("capture_sha256"),
        "provenance": {
            "origin": "projection",
            "detail_level": level,
            "base_template_ref": base_ref,
            "base_provenance": _strip_provenance(record.get("provenance")),
        },
        "category": record.get("category"),
        "missing_assets": record.get("missing_assets", []),
        "pin_policy": record.get("pin_policy"),
        "topology": _anonymize_topology(record, net_names, keep_master=level != "L1"),
    }
    if level == "L1":
        result["relations"] = derived["relations"]
        result["routing_style"] = derived["routing_style"]
        result["assets"] = {}
    else:
        result["assets"] = {
            name: _strip_asset(asset)
            for name, asset in record.get("assets", {}).items()
        }
        if level == "L3":
            devices, gaps = _parameters(record)
            result["parameters"] = {
                "schema": "cad.template.parameters.v1",
                "devices": devices,
                "gaps": gaps,
            }
    result["template_ref"] = "tpl_" + digest(
        {
            "base": base_ref,
            "rule": result["rule_version"],
            "level": level,
            "capture": record.get("capture_sha256"),
        }
    )
    from .template_build import summary  # local import: template_build imports this module's peers

    result["summary"] = summary(result)
    return result


def projection_summary(record):
    """Bounded facts for publication logs and tests."""
    text = canonical(record)
    return {
        "detail_level": record.get("detail_level"),
        "template_ref": record.get("template_ref"),
        "bytes": len(text.encode()),
        "assets": sorted(record.get("assets", {})),
        "has_relations": "relations" in record,
        "has_parameters": "parameters" in record,
        "source_keys": sorted(record.get("source", {})),
    }
