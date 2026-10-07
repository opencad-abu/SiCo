"""Bounded, non-evaluating reader for ADE statedb v5 text exports.

This is a source-settings reader, not an ADE execution or result authority.
SKILL expressions remain text; simulator tolerances never become design specs.
"""

from __future__ import annotations

import json
import math
from decimal import Decimal
from pathlib import Path
import re
import xml.etree.ElementTree as ET


_NUMBER = re.compile(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)([TGMKkmunpf]?)\Z")
_SCALE = {"": 1, "T": 1e12, "G": 1e9, "M": 1e6, "K": 1e3, "k": 1e3,
          "m": 1e-3, "u": 1e-6, "n": 1e-9, "p": 1e-12, "f": 1e-15}


def engineering_number(value: object) -> float:
    """Parse a finite scalar with explicit Spectre suffix semantics (M != m)."""
    if not isinstance(value, str) or not _NUMBER.fullmatch(value):
        raise ValueError("expected a literal engineering number")
    match = _NUMBER.fullmatch(value)
    result = float(Decimal(match[1]) * Decimal(str(_SCALE[match[2]])))
    if not math.isfinite(result):
        raise ValueError("engineering number is not finite")
    return result


def _value(field: ET.Element) -> str:
    text = (field.text or "").strip()
    if field.get("Type") == "string":
        try:
            result = json.loads(text)
        except (ValueError, TypeError) as exc:
            raise ValueError("unsupported statedb string literal") from exc
        if not isinstance(result, str):
            raise ValueError("invalid statedb string")
        return result
    return text


def _fields(parent: ET.Element | None) -> dict[str, str]:
    if parent is None:
        raise ValueError("required statedb section is missing")
    result = {}
    for child in parent.findall("field"):
        name = child.get("Name")
        if not name or name in result:
            raise ValueError("missing or duplicate statedb field name")
        result[name] = _value(child)
    return result


def read_statedb(path: Path) -> dict:
    data = path.read_bytes()
    if len(data) > 2 * 1024 * 1024 or b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise ValueError("unsafe or oversized statedb XML")
    root = ET.fromstring(data)
    if root.tag != "statedb" or root.get("version") != "5":
        raise ValueError("unsupported ADE statedb format")
    tests = root.findall("Test")
    if len(tests) != 1:
        raise ValueError("expected exactly one statedb test")
    test = tests[0]
    info = _fields(test.find("component[@Name='adeInfo']"))
    analyses = {}
    for analysis in test.findall("component[@Name='analyses']/analyses/analysis"):
        name = analysis.get("Name")
        if not name or name in analyses:
            raise ValueError("duplicate or unnamed analysis")
        fields = _fields(analysis.find("partition[@Name='fields']"))
        analyses[name] = {"enabled": fields.get("enable") == "(t)", "fields": fields,
                          "options": _fields(analysis.find("partition[@Name='options']"))}
    outputs = []
    for entry in test.findall("component[@Name='outputs']/partition[@Name='outputsCommon']/field[@Name='outputList']/field"):
        values = _fields(entry)
        outputs.append({key: values.get(key, "") for key in
                        ("name", "signal", "expression", "type", "save", "plot", "yaxisUnit", "waveSpec")})
    return {
        "schema_version": 1, "source_format": "cadence.statedb.v5",
        "ic_version": root.get("ICVersion"), "test": test.get("Name"),
        "design_info": info.get("designInfo"), "project_dir": info.get("projectDir"),
        "analyses": analyses,
        "model_setup": _fields(test.find("component[@Name='modelSetup']")),
        "simulator_options": _fields(test.find("component[@Name='simulatorOptions']/partition[@Name='opts']")),
        "outputs": outputs, "result_authority": False,
        "acceptance_tolerances": None,
    }


__all__ = ["engineering_number", "read_statedb"]
