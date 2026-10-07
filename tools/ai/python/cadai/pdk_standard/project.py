"""Conservative standard projection of observations; never grants design permission."""

import re
from decimal import Decimal
from pathlib import Path

from ..pdk_normalize import raw_value
from .jsonio import fingerprint
from .validate import ID, USES

UNITS = {"lengthMetric": "m", "areaMetric": "m2", "capacitance": "F", "resistance": "Ohm",
         "inductance": "H", "voltage": "V", "current": "A", "frequency": "Hz", "time": "s"}
NUMBER = re.compile(r"^([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)(meg|[fpnumkgt]?)$", re.I)
SCALES = {"": 1, "f": 1e-15, "p": 1e-12, "n": 1e-9, "u": 1e-6, "m": 1e-3,
          "k": 1e3, "meg": 1e6, "g": 1e9, "t": 1e12}


def unknown(reason):
    return {"state": "unknown", "reason": reason}


def safe_id(name):
    return name if isinstance(name, str) and ID.fullmatch(name) else "id_" + fingerprint(name)[7:31]


def parameter(row):
    cdf_type = row.get("cdf_type")
    typ = {"int": "integer", "float": "number", "boolean": "boolean",
           "string": "string", "radio": "string", "cyclic": "string"}.get(cdf_type)
    raw_unit = raw_value((row.get("units") or {}).get("cdf_raw"))
    parse_number = raw_value(row.get("parse_as_number")) in {"yes", "t", True}
    if cdf_type == "string" and parse_number:
        typ = "number"
    unit = UNITS.get(raw_unit, raw_unit if raw_unit in {"m", "m2", "V", "A", "F", "Ohm", "1"} else None)
    if typ in {"boolean", "string"}:
        unit = "1"
    default = (row.get("default") or {}).get("value")
    if (row.get("default") or {}).get("status") != "known" or default is None:
        default = unknown("CDF default unavailable")
    elif typ in {"number", "integer"} and isinstance(default, str):
        match = NUMBER.fullmatch(default.strip())
        default = float(Decimal(match[1]) * Decimal(str(SCALES[match[2].lower()]))) if match else unknown("Nonliteral CDF default")
        if typ == "integer" and isinstance(default, float):
            default = int(default) if default.is_integer() else unknown("Nonintegral CDF default")
    if unit is None:
        # Do not guess dimensionless simply because the CDF omitted its unit.
        unit = unknown("CDF physical unit requires confirmation")
    meaning = row.get("description") or row.get("prompt")
    return {"type": typ or unknown("Unsupported CDF value type"), "unit": unit,
            "meaning": meaning[:256] if meaning else unknown("Parameter meaning requires confirmation"),
            "write": "unknown", "requirement": "unknown", "default": default,
            "domain": {"kind": "unknown", "reason": "Effective legal values require confirmation"}}


def cdf(value, dependency):
    presence = value["parameters"].get("presence", "unknown")
    parameters = {p["name"]: parameter(p) for p in value["parameters"]["items"]
                  if p.get("parameter_kind") != "action"}
    return {"source": "capture", "depends_on": [dependency], "presence": presence,
            "parameters": parameters,
            "execution": {"mode": "literal" if presence == "absent" else "unknown"}}


def device(target, categories=()):
    return {"library": safe_id(target["library"]), "cell": target["cell"], "view": target["view"],
            "categories": [safe_id(c) for c in categories] or unknown("Category not observed"),
            "voltage": unknown("Voltage domain requires sourced confirmation"),
            "use": {use: "unknown" for use in sorted(USES)},
            "reason": "Cell usage has not been confirmed"}


def dependencies(value, device_id):
    target = {**value["target"], "library": safe_id(value["target"]["library"])}
    # Raw CDF excludes session handles. Stable observations, not old path-bound revision IDs.
    params = {p["name"]: p.get("raw", {}) for p in value["parameters"]["items"]}
    hooks = value["callbacks"].get("cell_metadata", {})
    return {device_id + "_cdf": {"kind": "cdf", "target": target,
                                 "fingerprint": fingerprint({"parameters": params, "metadata": hooks}),
                                 "method": "sico-effective-cdf-json-v1"},
            device_id + "_interface": {"kind": "interface", "target": target,
                "fingerprint": fingerprint(value["simulators"]["items"]),
                "method": "sico-siminfo-json-v1"}}


def files(index, library_path):
    root = Path(library_path).parent
    result = {}
    groups = [("document", index.get("documents", [])), ("runset", index.get("rule_decks", [])),
              ("model", index.get("deck_files", []))]
    for kind, rows in groups:
        for row in rows:
            path = row.get("path") or row.get("file") if isinstance(row, dict) else row
            if not isinstance(path, str):
                continue
            try:
                rel = Path(path).relative_to(root).as_posix()
            except ValueError:
                continue
            if ".." in Path(rel).parts:
                continue
            # Model discovery also returns README inputs. They are documentation
            # resources, not executable model includes.
            resource_kind = ('document' if kind == 'model' and Path(rel).suffix.lower() in {'.txt', '.md'}
                             and 'readme' in Path(rel).name.lower() else kind)
            key = "file_" + fingerprint(rel)[7:27]
            result[key] = {"kind": resource_kind, "root": "pdk", "path": rel,
                           "purpose": {"model": "Model declaration resource; loading configuration unconfirmed",
                                       "document": "PDK reference document", "runset": "Verification resource path"}[resource_kind]}
    return result
