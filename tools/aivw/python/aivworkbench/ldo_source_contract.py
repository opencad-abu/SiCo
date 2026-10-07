"""Validate TEST_LDO source settings without importing fixture behavior."""

from __future__ import annotations

import json
from pathlib import Path
import re

from .cadence_statedb import engineering_number, read_statedb
from .workspace import sha256_file, stable_digest


def _exact_keys(value: object, expected: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("%s fields are not exactly supported" % label)
    return value


def _finite_number(value: object, label: str) -> float:
    from math import isfinite
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError("%s must be a finite number" % label)
    return float(value)


def read_contract(path: Path) -> dict:
    if path.stat().st_size > 128 * 1024:
        raise ValueError("experiment contract exceeds byte budget")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("experiment contract root must be an object")
    required = {"schema_version", "contract_id", "scope", "behavioral_qualification", "source",
                "netlisting", "model", "analysis", "stimulus", "loads", "observations",
                "measurements", "unsupported"}
    if set(value) != required or value["schema_version"] != 1:
        raise ValueError("unsupported experiment contract schema")
    if (not isinstance(value["contract_id"], str)
            or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]{0,127}", value["contract_id"])
            or not isinstance(value["unsupported"], list)
            or any(not isinstance(item, str) or not item for item in value["unsupported"])):
        raise ValueError("experiment contract identity or unsupported list is malformed")
    if (value["scope"] != "testbench_transistor_characterization"
            or value["behavioral_qualification"] is not False
            or value["netlisting"] != {"mode": "r", "switch_views": ["spectre", "schematic"],
                "stop_views": ["spectre"], "config_policy": "explicit_schematic_characterization_not_auto_config_replay"}):
        raise ValueError("unsupported characterization scope or netlisting policy")
    source = _exact_keys(value["source"], {"library", "cell", "view", "state_view", "state_file", "state_sha256", "reference_run"}, "source")
    if source["library"] != "amsLDO" or source["cell"] != "TEST_LDO" or source["view"] != "schematic":
        raise ValueError("unsupported experiment source")
    if (source["state_view"] != "maestro" or source["state_file"] != "active.state"
            or not isinstance(source["state_sha256"], str)
            or not isinstance(source["reference_run"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", source["state_sha256"])
            or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", source["reference_run"])):
        raise ValueError("unsupported ADE state identity")
    model = _exact_keys(value["model"], {"relative_path", "section"}, "model")
    if (set(model) != {"relative_path", "section"}
            or not isinstance(model["relative_path"], str)
            or not isinstance(model["section"], str)
            or not re.fullmatch(r"[A-Za-z0-9_-]+\.scs", model["relative_path"])
            or not re.fullmatch(r"[A-Za-z0-9_]+", model["section"])):
        raise ValueError("unsafe model identity")
    stimulus = _exact_keys(value["stimulus"], {"supply", "enable"}, "stimulus")
    supply = _exact_keys(stimulus["supply"], {"instance", "positive", "negative", "value_v"}, "stimulus.supply")
    for key in ("instance", "positive", "negative"):
        if not isinstance(supply[key], str) or not supply[key]:
            raise ValueError("stimulus.supply.%s must be text" % key)
    _finite_number(supply["value_v"], "stimulus.supply.value_v")
    if not isinstance(stimulus["enable"], list) or not stimulus["enable"]:
        raise ValueError("stimulus.enable must be a non-empty array")
    for index, item in enumerate(stimulus["enable"]):
        item = _exact_keys(item, {"instance", "positive", "negative", "points"}, "stimulus.enable[%d]" % index)
        for key in ("instance", "positive", "negative"):
            if not isinstance(item[key], str) or not item[key]:
                raise ValueError("stimulus.enable[%d].%s must be text" % (index, key))
        points = item["points"]
        if not isinstance(points, list) or len(points) < 2:
            raise ValueError("stimulus.enable[%d].points must contain at least two points" % index)
        previous = None
        for point in points:
            if not isinstance(point, list) or len(point) != 2:
                raise ValueError("stimulus.enable[%d] has malformed PWL point" % index)
            timestamp = _finite_number(point[0], "PWL time")
            _finite_number(point[1], "PWL voltage")
            if previous is not None and timestamp <= previous:
                raise ValueError("PWL times must be strictly increasing")
            previous = timestamp
        if points[0][0] != 0:
            raise ValueError("PWL must start at time zero")
    loads = value["loads"]
    if not isinstance(loads, list) or not loads:
        raise ValueError("loads must be a non-empty array")
    for index, item in enumerate(loads):
        item = _exact_keys(item, {"instance", "positive", "negative", "resistance_ohm"}, "loads[%d]" % index)
        for key in ("instance", "positive", "negative"):
            if not isinstance(item[key], str) or not item[key]:
                raise ValueError("loads[%d].%s must be text" % (index, key))
        if _finite_number(item["resistance_ohm"], "load resistance") <= 0:
            raise ValueError("load resistance must be positive")
    observations = value["observations"]
    if not isinstance(observations, dict) or set(observations) != {"LDO_MASTER", "LDO_AON"}:
        raise ValueError("observations must declare both LDO variants")
    for cell, item in observations.items():
        item = _exact_keys(item, {"instance", "supply", "output", "load", "enable"} if cell == "LDO_AON" else {"instance", "supply", "output", "load"}, "observations.%s" % cell)
        for key, field in item.items():
            if not isinstance(field, str) or not field:
                raise ValueError("observations.%s.%s must be text" % (cell, key))
    m = _exact_keys(value["measurements"], {"format", "time_unit", "signals", "supply_current_sign", "sample_times_s", "extraction", "dataset", "acceptance_tolerances", "missing_tolerance_status", "settling_threshold"}, "measurements")
    if (m["acceptance_tolerances"] is not None or m["settling_threshold"] is not None
            or m["dataset"] != "characterization" or m["format"] != "csv"
            or m["time_unit"] != "s" or m["missing_tolerance_status"] != "BLOCKED_CONTRACT"
            or m["extraction"] != "linear_interpolation_between_bracketing_samples_no_extrapolation"
            or m["supply_current_sign"] != "negative_V0:p_is_current_delivered_to_testbench"):
        raise ValueError("characterization cannot silently acquire acceptance tolerances")
    if m["signals"] != {"VDD_EXT": "V", "VDD_INT": "V", "VDD_AON_OUT": "V", "VDD_PD2_OUT": "V", "net8": "V", "net9": "V", "V0:p": "A"}:
        raise ValueError("unsupported measurement signals or units")
    a = _exact_keys(value["analysis"], {"kind", "stop_s", "errpreset", "temperature_c", "reltol", "vabstol_v", "iabstol_a"}, "analysis")
    if a["kind"] != "tran" or a["errpreset"] != "moderate" or set(a) != {
        "kind", "stop_s", "errpreset", "temperature_c", "reltol", "vabstol_v", "iabstol_a"
    }:
        raise ValueError("unsupported Spectre analysis contract")
    # Numeric fields are accepted only as bounded finite JSON scalars.
    from math import isfinite
    if not isinstance(m["sample_times_s"], list):
        raise ValueError("measurement sample_times_s must be an array")
    for v in [a[k] for k in a if k not in {"kind", "errpreset"}] + m["sample_times_s"]:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not isfinite(v):
            raise ValueError("experiment numeric values must be finite")
    if not 0 < a["stop_s"] <= 0.01 or not 1 <= len(m["sample_times_s"]) <= 100:
        raise ValueError("experiment exceeds characterization budget")
    if any(not 0 <= v <= a["stop_s"] for v in m["sample_times_s"]):
        raise ValueError("measurement time is outside experiment")
    if any(right <= left for left, right in zip(m["sample_times_s"], m["sample_times_s"][1:])):
        raise ValueError("measurement times must be strictly increasing")
    for index, item in enumerate(stimulus["enable"]):
        if item["points"][-1][0] > a["stop_s"]:
            raise ValueError("stimulus.enable[%d] extends beyond experiment" % index)
    return value


def verify_readback(root: Path, contract: dict, interface: dict) -> dict:
    """Validate saved, complete double reads and compare stimuli to the contract."""
    from .ldo import canonicalize_interface
    if set(interface["variants"]) != {"LDO_MASTER", "LDO_AON"}:
        raise ValueError("source interface must declare both LDO variants")
    for cell, variant in interface["variants"].items():
        canonical = canonicalize_interface(interface, cell=cell)
        if canonical.source != "oa-cdf-interface-v1":
            raise ValueError("fixture interface cannot qualify source contract")
        for name, port in variant["ports"].items():
            if port["physical_domain"] != "electrical" or port["physical_unit"] != "V":
                raise ValueError("source port domain must remain electrical volts")
            if name == "EN" and port["threshold_contract"] is not None:
                raise ValueError("EN logic threshold qualification remains pending")
    files = sorted(root.glob("*-before.json"))
    expected = {"testbench", "stimulus", "LDO_MASTER-cdf", "LDO_AON-cdf",
                "LDO_MASTER-symbol", "LDO_AON-symbol"} | {
                    f"{cell}-schematic" for cell in ("LDO_MASTER", "LDO_AON", "LDO_PD2", "LDO_opamp", "LDO_INV")}
    if {p.name[:-12] for p in files} != expected:
        raise ValueError("incomplete source readback set")
    values = {}
    for path in files:
        if path.stat().st_size > 4 * 1024 * 1024:
            raise ValueError("readback exceeds byte limit")
        value = json.loads(path.read_text())
        second = path.with_name(path.name.replace("-before", "-after"))
        if second.stat().st_size > 4 * 1024 * 1024:
            raise ValueError("second readback exceeds byte limit")
        if value != json.loads(second.read_text()):
            raise ValueError("source changed between contract reads")
        label = path.name[:-12]
        if label not in {"LDO_MASTER-cdf", "LDO_AON-cdf"} and value.get("modified") is not False:
            raise ValueError("unsaved source in contract readback")
        if "kind" in value and (value.get("ok") is not True or value.get("truncated") is not False):
            raise ValueError("incomplete or truncated schematic/symbol")
        if "kind" in value:
            view = "symbol" if label.endswith("-symbol") else "schematic"
            cell = "TEST_LDO" if label == "testbench" else label.rsplit("-", 1)[0]
            if value.get("kind") != view or value.get("cellview") != {"lib": "amsLDO", "cell": cell, "view": view}:
                raise ValueError("source readback identity mismatch")
        values[label] = value
    for cell, variant in interface["variants"].items():
        symbol = values[cell + "-symbol"]
        expected_ports = variant["ports"]
        actual = {t["name"]: t["direction"] for t in symbol["terminals"]}
        wanted = {n: p["direction"] for n, p in expected_ports.items()}
        if actual != wanted or symbol["port_order"] != variant["port_order"]:
            raise ValueError("formal interface differs from OA symbol")
        schematic = {t["name"]: t["direction"] for t in values[cell + "-schematic"]["terminals"]}
        if schematic != wanted:
            raise ValueError("formal interface differs from OA schematic")
        order = values[cell + "-cdf"]["spectre_term_order"]
        if re.findall(r'"([A-Za-z_][A-Za-z_0-9]*)"', order) != variant["spectre_term_order"]:
            raise ValueError("Spectre CDF order disagrees with interface")
    instances = {x["name"]: x for x in values["testbench"]["instances"]}
    cdfs = {x["name"]: x["cdf"] for x in values["stimulus"]["instances"]}
    if (len(instances) != len(values["testbench"]["instances"])
            or len(cdfs) != len(values["stimulus"]["instances"]) or set(instances) != set(cdfs)):
        raise ValueError("duplicate or inconsistent testbench instance records")
    for cdf_record in values["stimulus"]["instances"]:
        inst = instances[cdf_record["name"]]
        if cdf_record["library"] != inst["lib"] or cdf_record["cell"] != inst["cell"]:
            raise ValueError("CDF master identity mismatch")
    def connection(spec, cell):
        inst = instances[spec["instance"]]
        terms = {t["name"]: t["net"] for t in inst["terminals"]}
        if inst["lib"] != "analogLib" or inst["cell"] != cell or terms != {"PLUS": spec["positive"], "MINUS": spec["negative"]}:
            raise ValueError("stimulus/load identity or connection mismatch")
        return cdfs[spec["instance"]]
    supply = contract["stimulus"]["supply"]
    if engineering_number(connection(supply, "vdc")["vdc"]) != supply["value_v"]:
        raise ValueError("supply voltage differs from TEST_LDO")
    for spec in contract["stimulus"]["enable"]:
        cdf = connection(spec, "vpwl")
        points = [[engineering_number(cdf[f"t{i}"]), engineering_number(cdf[f"v{i}"])]
                  for i in range(1, 51) if cdf.get(f"t{i}")]
        if points != spec["points"]:
            raise ValueError("enable PWL differs from TEST_LDO")
    for spec in contract["loads"]:
        if engineering_number(connection(spec, "res")["r"]) != spec["resistance_ohm"]:
            raise ValueError("output load differs from TEST_LDO")
    observed = contract["observations"]
    if set(observed) != {"LDO_MASTER", "LDO_AON"}:
        raise ValueError("both LDO observation mappings are required")
    for cell, spec in observed.items():
        inst = instances[spec["instance"]]
        wanted = {"VDD": spec["supply"], "VSS": "gnd!", "VOUT": spec["output"]}
        if cell == "LDO_AON":
            wanted["EN"] = spec["enable"]
        if inst["lib"] != "amsLDO" or inst["cell"] != cell or {t["name"]: t["net"] for t in inst["terminals"]} != wanted:
            raise ValueError("LDO observation mapping differs from TEST_LDO")
    # The whole connection/CDF readback is bound, including the MASTER cascade.
    return {"digest": stable_digest(values), "records": {p.name: sha256_file(p) for p in files}}


def verify_state(path: Path, contract: dict) -> dict:
    if sha256_file(path) != contract["source"]["state_sha256"]:
        raise ValueError("ADE state changed; experiment contract needs recalibration")
    state = read_statedb(path)
    a = contract["analysis"]
    enabled = {n for n, v in state["analyses"].items() if v["enabled"]}
    if enabled != {"tran"}:
        raise ValueError("expected exactly one enabled transient analysis")
    tran = state["analyses"]["tran"]["fields"]
    if engineering_number(tran["stop"]) != a["stop_s"] or tran["errpreset"] != a["errpreset"]:
        raise ValueError("analysis settings differ from ADE")
    options = state["simulator_options"]
    for source, field in {"temp": "temperature_c", "reltol": "reltol", "vabstol": "vabstol_v", "iabstol": "iabstol_a"}.items():
        if engineering_number(options[source]) != a[field]:
            raise ValueError("simulator option differs from ADE")
    if state["model_setup"]["modelFiles"] != f'(("models/spectre/{contract["model"]["relative_path"]}" "{contract["model"]["section"]}"))':
        raise ValueError("model file or section differs from ADE")
    return state
