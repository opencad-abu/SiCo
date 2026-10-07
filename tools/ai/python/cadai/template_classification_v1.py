"""Frozen explicit-evidence v1 compatibility for immutable archived captures.

Used only by the versioned classifier; retain until no v1 archive needs replay.
"""

import copy

from .circuit_spec_schema import MASTER, CircuitSpecError, validate
from .template_schema import TemplateError

LEGACY_SEMANTICS = "explicit_evidence_v1"
LEGACY_RULE_VERSION = "20260922.classification.v1"
CLASSIFICATION = MASTER["properties"]["classification"]
_BASIC = {"ipin": "port_graphic", "opin": "port_graphic", "iopin": "port_graphic",
          "noConn": "no_connect", "gnd": "supply_marker", "vdd": "supply_marker"}
_ANALOG = {"vdc": "voltage_source", "vac": "voltage_source", "vpulse": "voltage_source",
           "idc": "current_source", "ipwl": "current_source", "res": "resistor",
           "cap": "capacitor", "ind": "inductor", "gnd": "supply_marker",
           "vdd": "supply_marker"}


def classify_v1(instance, pin_names, evidence=None):
    """Names/terminal shapes alone never establish a project device's electrical type."""
    cell = instance["cellName"]
    builtin = instance.get("builtin_library")
    if builtin not in (None, "basic", "analogLib"):
        raise TemplateError("unsupported builtin library observation")
    if builtin == "basic" and cell in _BASIC:
        if evidence:
            raise TemplateError("classification cannot promote a builtin graphic")
        return _BASIC[cell], _BASIC[cell], {}
    if instance.get("schematic_available"):
        if evidence:
            raise TemplateError("classification cannot promote hierarchy source roles")
        return "hierarchy", "subcircuit", {"master": "/".join(
            instance[key] for key in ("libName", "cellName", "viewName")
        )}
    known = _ANALOG.get(cell) if builtin == "analogLib" else None
    if evidence:
        try:
            validate(evidence, CLASSIFICATION, "source classification")
        except CircuitSpecError as exc:
            raise TemplateError(str(exc)) from exc
        if evidence["kind"] in {"unknown", "port_graphic", "no_connect", "supply_marker"}:
            raise TemplateError("explicit device classification must identify an electrical device")
        if not instance.get("master_available") or not instance.get("terminal_names"):
            raise TemplateError("classification requires an observed master and terminal inventory")
        if known and (evidence["kind"] != known or known == "supply_marker"):
            raise TemplateError("classification conflicts with installed builtin evidence")
        return "device", evidence["kind"], copy.deepcopy(evidence["attributes"])
    if known:
        return ("supply_marker" if known == "supply_marker" else "device"), known, {
            "device_class": cell
        }
    return "unknown", "unknown", {"master": "/".join(
        instance[key] for key in ("libName", "cellName", "viewName")
    )}
