"""Declarative standalone electrical experiments for Spectre block evidence.

Pin names, pin order, DC/PWL sources, resistive load and numerical controls are
data. This adapter contains no circuit-family equations or nominal values.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Mapping

from .acceptance import finite, validate_tolerance


IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def identifier(value: object) -> str:
    if not isinstance(value, str) or not IDENT.fullmatch(value):
        raise ValueError("electrical identifier is unsafe")
    return value


def validate_experiment(value: Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(value, Mapping) and value.get("schema_version") == 2:
        from .dynamic_experiment import validate_dynamic_experiment
        return validate_dynamic_experiment(value)
    fields = {"schema_version", "kind", "experiment_id", "target", "interface_sha256",
              "roles", "analysis", "model", "cases", "acceptance", "sampling_policy"}
    if not isinstance(value, Mapping) or set(value) != fields or type(value["schema_version"]) is not int or value["schema_version"] != 1 or value["kind"] != "electrical-block-experiment":
        raise ValueError("unsupported electrical experiment schema")
    identifier(value["experiment_id"])
    target = value["target"]
    if set(target) != {"library", "cell", "term_order"}:
        raise ValueError("target identity fields differ")
    identifier(target["library"]); identifier(target["cell"])
    pins = target["term_order"]
    if not isinstance(pins, list) or not pins or len(set(pins)) != len(pins):
        raise ValueError("terminal order is missing or duplicated")
    for pin in pins:
        identifier(pin)
    if not isinstance(value["interface_sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", value["interface_sha256"]):
        raise ValueError("interface hash is required")
    roles = value["roles"]
    if set(roles) != {"supply", "reference", "output", "controls"} or not isinstance(roles["controls"], list):
        raise ValueError("electrical roles are incomplete")
    assigned = [roles[n] for n in ("supply", "reference", "output")] + roles["controls"]
    if len(set(assigned)) != len(assigned) or set(assigned) != set(pins):
        raise ValueError("every terminal must have one explicit role")
    analysis = value["analysis"]
    if set(analysis)-{"transient_resolution"} != {"stop_s", "maxstep_s", "temperature_c", "reltol", "vabstol_v", "iabstol_a", "errpreset", "sample_times_s", "steady_window_s"}:
        raise ValueError("analysis controls are incomplete")
    for name in ("stop_s", "maxstep_s", "reltol", "vabstol_v", "iabstol_a"):
        if finite(analysis[name], name) <= 0:
            raise ValueError("analysis controls must be positive")
    finite(analysis["temperature_c"], "temperature_c")
    if analysis["errpreset"] not in {"liberal", "moderate", "conservative"}:
        raise ValueError("unsupported errpreset")
    stop = analysis["stop_s"]
    if stop > 0.1 or analysis["maxstep_s"] > stop:
        raise ValueError("analysis exceeds experiment budget")
    if "transient_resolution" in analysis:
        from .transient_resolution import validate
        validate(analysis["transient_resolution"],analysis)
    samples = analysis["sample_times_s"]
    window = analysis["steady_window_s"]
    if not isinstance(samples, list) or not 2 <= len(samples) <= 10000:
        raise ValueError("bounded measurement sample times are required")
    for t in samples:
        if not 0 <= finite(t, "sample time") <= stop:
            raise ValueError("measurement time outside experiment")
    if any(b <= a for a, b in zip(samples, samples[1:])):
        raise ValueError("measurement times must strictly increase")
    if not isinstance(window, list) or len(window) != 2 or not 0 <= finite(window[0], "window start") < finite(window[1], "window end") <= stop:
        raise ValueError("steady observation window is invalid")
    if window[0] not in samples or window[1] not in samples:
        raise ValueError("steady observation window endpoints must be sampled")
    model = value["model"]
    if set(model) != {"relative_path", "section"} or not re.fullmatch(r"[A-Za-z0-9_-]+\.scs", model["relative_path"]):
        raise ValueError("model path must be a literal basename")
    identifier(model["section"])
    acceptance = value["acceptance"]
    if set(acceptance) != {"decision", "decision_sha256", "nominal_supply_v", "target_v", "product", "correlation"}:
        raise ValueError("acceptance decision is incomplete")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+\.json", acceptance["decision"]) or not re.fullmatch(r"[a-f0-9]{64}", acceptance["decision_sha256"]):
        raise ValueError("acceptance decision must be hash-bound")
    finite(acceptance["nominal_supply_v"], "nominal supply")
    if finite(acceptance["target_v"], "product target") <= 0:
        raise ValueError("product reference must be positive")
    for kind in ("product", "correlation"):
        if validate_tolerance(acceptance[kind])["unit"] != "V":
            raise ValueError("output acceptance must have voltage units")
    if value["sampling_policy"] != "public_characterization_whole_cases":
        raise ValueError("this execution path only accepts public characterization")
    cases = value["cases"]
    if not isinstance(cases, list) or not 1 <= len(cases) <= 256:
        raise ValueError("case count outside budget")
    ids = set()
    for case in cases:
        if set(case) != {"id", "kind", "purpose", "voltages", "load_ohm", "product_check"}:
            raise ValueError("case fields differ from experiment schema")
        identifier(case["id"])
        if case["id"] in ids:
            raise ValueError("duplicate experiment case")
        ids.add(case["id"])
        identifier(case["purpose"])
        if case["kind"] not in {"dc", "transient"} or type(case["product_check"]) is not bool:
            raise ValueError("unsupported case kind/product applicability")
        if finite(case["load_ohm"], "load_ohm") <= 0:
            raise ValueError("resistive load must be positive")
        sources = case["voltages"]
        if set(sources) != set(pins) - {roles["output"]}:
            raise ValueError("every input terminal requires an explicit stimulus")
        for pin, source in sources.items():
            if set(source) == {"dc_v"}:
                finite(source["dc_v"], "dc voltage")
            elif set(source) == {"pwl"} and case["kind"] == "transient":
                points = source["pwl"]
                if not isinstance(points, list) or not 2 <= len(points) <= 64:
                    raise ValueError("PWL source needs bounded points")
                for point in points:
                    if not isinstance(point, list) or len(point) != 2:
                        raise ValueError("PWL point must be [time_s, voltage_v]")
                    finite(point[0], "PWL time"); finite(point[1], "PWL voltage")
                if points[0][0] != 0 or points[-1][0] != stop or any(b[0] <= a[0] for a,b in zip(points, points[1:])):
                    raise ValueError("PWL must cover the experiment with increasing time")
            else:
                raise ValueError("unsupported voltage source")
        if sources[roles["reference"]] != {"dc_v": 0.0}:
            raise ValueError("this adapter supports a fixed zero reference only")
        if case["product_check"] and (case["kind"] != "dc" or sources[roles["supply"]] != {"dc_v": acceptance["nominal_supply_v"]}):
            raise ValueError("nominal product gate applies only to DC at nominal supply")
    return json.loads(json.dumps(value, allow_nan=False))


def read_experiment(path: Path) -> dict[str, Any]:
    return validate_experiment(json.loads(path.read_text(encoding="utf-8")))


def extract_subcircuits(text: str, target: Mapping[str, Any]) -> str:
    identifier(target["cell"])
    blocks = re.findall(r"(?ms)^subckt\s+[A-Za-z_][A-Za-z0-9_]*\s+[^\n]+\n.*?^ends\s+[A-Za-z_][A-Za-z0-9_]*\s*$", text)
    declarations = re.findall(r"^subckt\s+" + re.escape(target["cell"]) + r"\s+([^\n]+)$", text, re.M)
    if len(declarations) != 1 or declarations[0].split() != target["term_order"]:
        raise ValueError("source subcircuit terminal order differs from experiment")
    return "\n\n".join(blocks) + "\n"


def render_deck(experiment: Mapping[str, Any], case_id: str) -> str:
    if experiment.get("schema_version") == 2:
        from .dynamic_experiment import render_dynamic_deck
        return render_dynamic_deck(experiment, case_id)
    plan = validate_experiment(experiment)
    cases = [c for c in plan["cases"] if c["id"] == case_id]
    if len(cases) != 1:
        raise ValueError("case is not present in experiment")
    case = cases[0]; roles = plan["roles"]; a = plan["analysis"]
    supply = roles["supply"]
    probe_node = "aivw_internal_supply"
    if probe_node in plan["target"]["term_order"]:
        raise ValueError("reserved probe node collides with terminal")
    lines = ["simulator lang=spectre", "global 0",
             'include "../../models/%s" section=%s' % (plan["model"]["relative_path"], plan["model"]["section"]),
             'include "../../circuit.scs"']
    for pin, source in case["voltages"].items():
        node = probe_node if pin == supply else pin
        prefix = "VS_%s (%s 0) vsource " % (pin, node)
        if "dc_v" in source:
            lines.append(prefix + "dc=%.17g" % source["dc_v"])
        else:
            lines.append(prefix + "type=pwl wave=[ " + " ".join("%.17g %.17g" % tuple(p) for p in source["pwl"]) + " ]")
    lines += ["VPROBE (%s %s) vsource dc=0" % (probe_node, supply),
              "XUUT (%s) %s" % (" ".join(plan["target"]["term_order"]), plan["target"]["cell"]),
              "RLOAD (%s %s) resistor r=%.17g" % (roles["output"], roles["reference"], case["load_ohm"]),
              "simulatorOptions options temp=%.17g reltol=%.17g vabstol=%.17g iabstol=%.17g" % (a["temperature_c"], a["reltol"], a["vabstol_v"], a["iabstol_a"]),
              "tran tran stop=%.17g errpreset=%s maxstep=%.17g" % (a["stop_s"], a["errpreset"], a["maxstep_s"]),
              "saveOptions options save=selected",
              "save %s VPROBE:p" % " ".join(plan["target"]["term_order"])]
    return "\n".join(lines) + "\n"


def ocean_export(psf: Path, output: Path, signals: Mapping[str, str]) -> str:
    """Export selected physical signals without imposing digital pin semantics."""
    header = ",".join(["time[s]"] + ["%s[%s]" % (n,u) for n,u in signals.items()])
    names = " ".join(json.dumps(name) for name in signals)
    return '''envSetVal("asimenv.startup" "projectDir" 'string %s)
resultsDir(%s)
procedure(aivwExport()
  let((waves x out n i waveX)
    openResults(%s) selectResult('tran)
    waves=foreach(mapcar name list(%s) getData(name ?result 'tran))
    unless(forall(wave waves drIsWaveform(wave)) error("missing waveform"))
    x=drGetWaveformXVec(car(waves)) n=drVectorLength(x)
    unless(and(n>=2 n<=250000) error("waveform sample bound"))
    out=outfile(%s "w") fprintf(out "%s\\n")
    for(i 0 n-1
      fprintf(out "%%.17g" drGetElem(x i))
      foreach(wave waves
        waveX=drGetWaveformXVec(wave)
        unless(drGetElem(waveX i)==drGetElem(x i) error("time axes differ"))
        fprintf(out ",%%.17g" drGetElem(drGetWaveformYVec(wave) i)))
      fprintf(out "\\n")) close(out) printf("AIVW_WAVEFORM_EXPORT_COMPLETE\\n") t))
if(errset(aivwExport() t) then exit(0) else exit(2))
''' % (json.dumps(str(psf.parent)), json.dumps(str(psf)), json.dumps(str(psf)), names, json.dumps(str(output)), header)
