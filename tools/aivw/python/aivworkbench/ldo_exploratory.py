"""Source-derived LDO exploratory characterization plans.

The planner is deliberately an observation boundary.  It describes bounded
Spectre cases derived from the calibrated interface and the real TEST_LDO
nominal setup, but it never creates an acceptance tolerance or a behavior
verdict.  A later executor may consume the plan and place raw results in an
isolated payload; the returned contract remains ``BLOCKED_CONTRACT`` until a
project owner approves the behavioral specification.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Mapping


class LDOExplorationError(ValueError):
    """Malformed or unsafe exploratory characterization input."""


_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_TOPOLOGIES = ("LDO_MASTER", "LDO_AON")
_POLICY_FIELDS = {
    "schema_version", "kind", "policy_id", "status", "source", "model",
    "analysis", "topologies", "matrix", "measurements", "tolerance_contract",
    "holdout",
}
_SOURCE_FIELDS = {
    "interface_contract", "experiment_contract", "testbench", "reference_run",
}
_MODEL_FIELDS = {"relative_path", "section"}
_ANALYSIS_FIELDS = {"temperature_c", "errpreset", "max_stop_s"}
_TOPOLOGY_FIELDS = {"ports", "vdd_window_v", "load_resistance_ohm", "en_voltage_v"}
_WINDOW_FIELDS = {"min", "max", "probe_margin"}
_LOAD_FIELDS = {"nominal", "values"}
_EN_FIELDS = {"nominal", "values"}
_MATRIX_FIELDS = {"supply_sweep", "load_sweep", "enable_voltage_sweep", "transient"}
_TRANSIENT_FIELDS = {"startup", "enable_rise", "enable_fall", "supply_step"}
_MEASUREMENT_FIELDS = {"signals", "sample_times_s", "time_unit", "extraction"}
_TOLERANCE_FIELDS = {"status", "metrics", "reason"}
_HOLDOUT_FIELDS = {"locked", "public_values_included", "partition_policy"}
_CASE_FIELDS = {"id", "topology", "kind", "purpose", "stimulus", "measurements"}


def _exact(value: object, fields: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise LDOExplorationError("%s fields are not exactly supported" % label)
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or not _ID.fullmatch(value):
        raise LDOExplorationError("%s must be a bounded identifier" % label)
    return value


def _finite(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LDOExplorationError("%s must be numeric" % label)
    result = float(value)
    if not math.isfinite(result):
        raise LDOExplorationError("%s must be finite" % label)
    return result


def _positive(value: object, label: str) -> float:
    result = _finite(value, label)
    if result <= 0.0:
        raise LDOExplorationError("%s must be positive" % label)
    return result


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _number_array(value: object, label: str, *, positive: bool = False) -> tuple[float, ...]:
    if not isinstance(value, list) or not value:
        raise LDOExplorationError("%s must be a non-empty numeric array" % label)
    result = tuple(_finite(item, "%s[%d]" % (label, index)) for index, item in enumerate(value))
    if any(right <= left for left, right in zip(result, result[1:])):
        raise LDOExplorationError("%s must be strictly increasing" % label)
    if positive and any(item <= 0.0 for item in result):
        raise LDOExplorationError("%s must contain positive values" % label)
    return result


def _validate_source(value: object) -> dict[str, str]:
    source = _exact(value, _SOURCE_FIELDS, "source")
    result = {name: str(source[name]) for name in _SOURCE_FIELDS}
    for name in _SOURCE_FIELDS:
        if not result[name] or (name.endswith("contract") and not re.fullmatch(r"[a-f0-9]{64}", result[name])):
            raise LDOExplorationError("source.%s is malformed" % name)
    return result


def _validate_topology(value: object, topology: str) -> dict[str, Any]:
    item = _exact(value, _TOPOLOGY_FIELDS, "topologies.%s" % topology)
    ports = item["ports"]
    expected = ["VDD", "VSS", "VOUT"] if topology == "LDO_MASTER" else ["VDD", "VSS", "EN", "VOUT"]
    if ports != expected:
        raise LDOExplorationError("%s ports differ from source interface" % topology)
    window = _exact(item["vdd_window_v"], _WINDOW_FIELDS, "vdd_window_v")
    lower = _finite(window["min"], "vdd_window_v.min")
    upper = _finite(window["max"], "vdd_window_v.max")
    margin = _positive(window["probe_margin"], "vdd_window_v.probe_margin")
    if not lower < upper or margin >= (upper - lower) / 2.0:
        raise LDOExplorationError("%s VDD window is invalid" % topology)
    load = _exact(item["load_resistance_ohm"], _LOAD_FIELDS, "load_resistance_ohm")
    nominal = _positive(load["nominal"], "load_resistance_ohm.nominal")
    loads = _number_array(load["values"], "load_resistance_ohm.values", positive=True)
    if nominal not in loads:
        raise LDOExplorationError("%s load sweep must contain nominal load" % topology)
    en = _exact(item["en_voltage_v"], _EN_FIELDS, "en_voltage_v")
    if topology == "LDO_MASTER":
        if en["nominal"] is not None or en["values"] is not None:
            raise LDOExplorationError("LDO_MASTER cannot declare EN probes")
    else:
        _finite(en["nominal"], "en_voltage_v.nominal")
        _number_array(en["values"], "en_voltage_v.values")
    return {
        "ports": list(ports),
        "vdd_window_v": {"min": lower, "max": upper, "probe_margin": margin},
        "load_resistance_ohm": {"nominal": nominal, "values": list(loads)},
        "en_voltage_v": {"nominal": en["nominal"], "values": None if en["values"] is None else list(en["values"])},
    }


def read_exploratory_policy(path: Path) -> dict[str, Any]:
    """Read and validate one topology's source-derived policy manifest."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LDOExplorationError("cannot read exploratory policy: %s" % exc) from exc
    root = _exact(value, _POLICY_FIELDS, "exploratory policy")
    if root["schema_version"] != 1 or root["kind"] != "ldo-exploratory-policy":
        raise LDOExplorationError("unsupported exploratory policy schema")
    policy_id = _text(root["policy_id"], "policy_id")
    if root["status"] != "source_derived_exploration_pending":
        raise LDOExplorationError("exploratory policy must remain pending")
    source = _validate_source(root["source"])
    model = _exact(root["model"], _MODEL_FIELDS, "model")
    if not re.fullmatch(r"[A-Za-z0-9_-]+\.scs", str(model["relative_path"])) or not _ID.fullmatch(str(model["section"])):
        raise LDOExplorationError("model identity is unsafe")
    analysis = _exact(root["analysis"], _ANALYSIS_FIELDS, "analysis")
    if analysis["errpreset"] != "moderate" or _finite(analysis["temperature_c"], "temperature_c") != 27.0:
        raise LDOExplorationError("exploration must use the calibrated ADE solver/temperature")
    if not 0.0 < _finite(analysis["max_stop_s"], "max_stop_s") <= 0.1:
        raise LDOExplorationError("exploration stop budget is unsafe")
    topologies = root["topologies"]
    if not isinstance(topologies, Mapping) or len(topologies) != 1:
        # The exploratory manifest is intentionally one topology per file.
        raise LDOExplorationError("policy must contain exactly one topology")
    topology = next(iter(topologies))
    if topology not in _TOPOLOGIES:
        raise LDOExplorationError("unsupported exploratory topology")
    normalized_topology = _validate_topology(topologies[topology], topology)
    matrix = _exact(root["matrix"], _MATRIX_FIELDS, "matrix")
    for field in ("supply_sweep", "load_sweep", "enable_voltage_sweep"):
        if not isinstance(matrix[field], bool):
            raise LDOExplorationError("matrix.%s must be boolean" % field)
    transient = _exact(matrix["transient"], _TRANSIENT_FIELDS, "matrix.transient")
    for name, declaration in transient.items():
        if not isinstance(declaration, bool):
            raise LDOExplorationError("matrix.transient.%s must be boolean" % name)
    if topology == "LDO_MASTER" and matrix["enable_voltage_sweep"]:
        raise LDOExplorationError("LDO_MASTER cannot enable EN voltage sweep")
    if topology == "LDO_AON" and not matrix["enable_voltage_sweep"]:
        raise LDOExplorationError("LDO_AON requires EN voltage sweep")
    measurements = _exact(root["measurements"], _MEASUREMENT_FIELDS, "measurements")
    signals = measurements["signals"]
    if not isinstance(signals, Mapping) or not signals or any(not isinstance(k, str) or not isinstance(v, str) for k, v in signals.items()):
        raise LDOExplorationError("measurement signals must be a unit map")
    if "VOUT" not in signals.values() and "VOUT" not in signals:
        raise LDOExplorationError("measurements must include VOUT")
    times = _number_array(measurements["sample_times_s"], "measurement sample_times_s")
    if measurements["time_unit"] != "s" or measurements["extraction"] != "linear_interpolation_between_bracketing_samples_no_extrapolation":
        raise LDOExplorationError("measurement units/extraction differ from calibrated contract")
    tolerance = _exact(root["tolerance_contract"], _TOLERANCE_FIELDS, "tolerance_contract")
    if tolerance["status"] != "BLOCKED_CONTRACT" or tolerance["metrics"] != {} or not isinstance(tolerance["reason"], str) or not tolerance["reason"]:
        raise LDOExplorationError("exploratory policy cannot contain acceptance tolerances")
    holdout = _exact(root["holdout"], _HOLDOUT_FIELDS, "holdout")
    if holdout != {"locked": True, "public_values_included": False, "partition_policy": "hidden_holdout_excluded_from_exploration_plan"}:
        raise LDOExplorationError("holdout must remain locked")
    return {
        "schema_version": 1,
        "kind": "ldo-exploratory-policy",
        "policy_id": policy_id,
        "status": root["status"],
        "source": source,
        "model": {"relative_path": str(model["relative_path"]), "section": str(model["section"])},
        "analysis": {"temperature_c": 27.0, "errpreset": "moderate", "max_stop_s": float(analysis["max_stop_s"])},
        "topology": topology,
        "topologies": {topology: normalized_topology},
        "matrix": {**{name: bool(matrix[name]) for name in ("supply_sweep", "load_sweep", "enable_voltage_sweep")}, "transient": {name: bool(transient[name]) for name in _TRANSIENT_FIELDS}},
        "measurements": {"signals": dict(signals), "sample_times_s": list(times), "time_unit": "s", "extraction": measurements["extraction"]},
        "tolerance_contract": {"status": "BLOCKED_CONTRACT", "metrics": {}, "reason": tolerance["reason"]},
        "holdout": dict(holdout),
    }


@dataclass(frozen=True)
class ExploratoryCase:
    case_id: str
    topology: str
    kind: str
    purpose: str
    stimulus: Mapping[str, Any]
    measurements: Mapping[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.case_id, "topology": self.topology, "kind": self.kind,
                "purpose": self.purpose, "dataset": "exploratory",
                "stimulus": dict(self.stimulus),
                "measurements": dict(self.measurements)}


def build_exploratory_plan(policy: Mapping[str, Any]) -> dict[str, Any]:
    """Build the deterministic public matrix for one topology."""
    topology = str(policy["topology"])
    spec = policy["topologies"][topology]
    window = spec["vdd_window_v"]
    load = spec["load_resistance_ohm"]
    cases: list[ExploratoryCase] = []
    measurements = policy["measurements"]["signals"]
    if policy["matrix"]["supply_sweep"]:
        lower, upper, margin = window["min"], window["max"], window["probe_margin"]
        values = (lower - margin, lower, (lower + upper) / 2.0, upper, upper + margin)
        for index, voltage in enumerate(values):
            stimulus = {"vdd_v": voltage, "vss_v": 0.0, "load_resistance_ohm": load["nominal"]}
            if topology == "LDO_AON":
                stimulus["en_v"] = spec["en_voltage_v"]["nominal"]
            cases.append(ExploratoryCase("dc-supply-%02d" % index, topology, "dc", "supply_sweep", stimulus, measurements))
    if policy["matrix"]["load_sweep"]:
        for index, resistance in enumerate(load["values"]):
            stimulus = {"vdd_v": window["min"] + (window["max"] - window["min"]) / 2.0,
                        "vss_v": 0.0, "load_resistance_ohm": resistance}
            if topology == "LDO_AON":
                stimulus["en_v"] = spec["en_voltage_v"]["nominal"]
            cases.append(ExploratoryCase("dc-load-%02d" % index, topology, "dc", "load_sweep", stimulus, measurements))
    if topology == "LDO_AON" and policy["matrix"]["enable_voltage_sweep"]:
        for index, voltage in enumerate(spec["en_voltage_v"]["values"]):
            cases.append(ExploratoryCase("dc-enable-%02d" % index, topology, "dc", "enable_voltage_sweep",
                                         {"vdd_v": window["min"] + (window["max"] - window["min"]) / 2.0,
                                          "vss_v": 0.0, "load_resistance_ohm": load["nominal"], "en_v": voltage}, measurements))
    if policy["matrix"]["transient"]["startup"]:
        cases.append(ExploratoryCase("tran-startup", topology, "transient", "startup_ramp",
                                     {"initial_vdd_v": 0.0, "final_vdd_v": window["min"] + (window["max"] - window["min"]) / 2.0,
                                      "load_resistance_ohm": load["nominal"]}, measurements))
    if topology == "LDO_AON":
        if policy["matrix"]["transient"]["enable_rise"]:
            cases.append(ExploratoryCase("tran-enable-rise", topology, "transient", "enable_rise",
                                         {"vdd_v": spec["vdd_window_v"]["min"] + 0.5, "initial_en_v": 0.0, "final_en_v": spec["en_voltage_v"]["nominal"], "load_resistance_ohm": load["nominal"]}, measurements))
        if policy["matrix"]["transient"]["enable_fall"]:
            cases.append(ExploratoryCase("tran-enable-fall", topology, "transient", "enable_fall",
                                         {"vdd_v": spec["vdd_window_v"]["min"] + 0.5, "initial_en_v": spec["en_voltage_v"]["nominal"], "final_en_v": 0.0, "load_resistance_ohm": load["nominal"]}, measurements))
    if policy["matrix"]["transient"]["supply_step"]:
        cases.append(ExploratoryCase("tran-supply-step", topology, "transient", "supply_step",
                                     {"initial_vdd_v": window["min"], "final_vdd_v": window["max"], "load_resistance_ohm": load["nominal"]}, measurements))
    if not cases:
        raise LDOExplorationError("exploratory matrix selected no cases")
    result = {"schema_version": 1, "kind": "ldo-exploratory-plan", "topology": topology,
              "policy_id": policy["policy_id"], "policy_digest": _digest(policy),
              "holdout": dict(policy["holdout"]), "cases": [case.to_dict() for case in cases],
              "tolerance_contract": dict(policy["tolerance_contract"]),
              "behavior_verdict": "NOT_ESTABLISHED", "correlation_status": "BLOCKED_CONTRACT"}
    result["plan_digest"] = _digest({key: value for key, value in result.items() if key != "plan_digest"})
    return result


__all__ = ["LDOExplorationError", "ExploratoryCase", "read_exploratory_policy", "build_exploratory_plan"]
