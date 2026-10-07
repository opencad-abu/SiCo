"""Measured, separable DC surface candidate for continuously valued ports.

Every knot is a source measurement. The model has no circuit-specific defaults
or equations. Resistive loading is a model parameter, not an invented OA pin.
Only the declared static domain and fixed control voltages are supported.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..acceptance import finite
from ..electrical_experiment import identifier
from ..workspace import stable_digest


def calibrate_surface(experiment: Mapping[str, Any], evidence: Mapping[str, Any]) -> dict[str, Any]:
    roles = experiment["roles"]
    supply, reference, output = (roles[k] for k in ("supply", "reference", "output"))
    by_id = {item["id"]:item for item in evidence["cases"]}
    points=[]
    for case in experiment["cases"]:
        if case["kind"] != "dc" or case["purpose"] not in {"nominal_load", "supply_probe"}:
            continue
        measurements = by_id[case["id"]]["measurements"]
        sample = measurements["samples"][-1]["values"]
        if measurements["units"][output] != "V":
            raise ValueError("DC surface requires volt-valued measurements")
        points.append({"voltage":finite(case["voltages"][supply]["dc_v"], "supply"),
                       "load_ohm":finite(case["load_ohm"], "load"),
                       "output":finite(sample[output], "output"),
                       "reference":finite(case["voltages"][reference]["dc_v"], "reference"),
                       "controls":{pin:finite(case["voltages"][pin]["dc_v"], "control") for pin in roles["controls"]}})
    if len(points)<5 or any(p["load_ohm"]<=0 for p in points):
        raise ValueError("surface needs at least five measured points and positive loads")
    controls = points[0]["controls"]
    if any(p["controls"]!=controls or p["reference"]!=0 for p in points):
        raise ValueError("DC surface only supports fixed controls and zero reference")
    nominal=experiment["acceptance"]["nominal_supply_v"]
    varied=[p for p in points if p["voltage"]!=nominal]
    if len({p["load_ohm"] for p in varied})!=1:
        raise ValueError("supply sweep must share one reference load")
    load0=varied[0]["load_ohm"]
    center=[p for p in points if p["voltage"]==nominal and p["load_ohm"]==load0]
    if len(center)!=1:
        raise ValueError("unique nominal center required")
    supply_knots=sorted([[p["voltage"],p["output"]] for p in points if p["load_ohm"]==load0])
    load_knots=sorted([[1/p["load_ohm"],p["output"]-center[0]["output"]] for p in points if p["voltage"]==nominal])
    for knots in (supply_knots,load_knots):
        if len(knots)<3 or any(b[0]<=a[0] for a,b in zip(knots,knots[1:])):
            raise ValueError("surface axes require three distinct measured knots")
    result={"schema_version":1,"model_class":"measured_dc_surface","version":1,
            "module":identifier(experiment["target"]["cell"]),"ports":experiment["target"]["term_order"],"roles":dict(roles),
            "supply_knots":supply_knots,"conductance_delta_knots":load_knots,"reference_load_ohm":load0,
            "fixed_controls_v":controls,"vss_v":0.,"load_range_ohm":[min(p["load_ohm"] for p in points),max(p["load_ohm"] for p in points)],
            "calibration_case_count":len(points),"source_generation":evidence["source_generation"],
            "experiment_digest":evidence["experiment_digest"],"output_unit":"V",
            "scope":"static_dc_fixed_controls","unsupported":["transient","control_transition","nonzero_reference","outside_measured_domain","current_output"],
            "generation_mode":"deterministic_measured_surface","ai_generation":"not_performed_for_registered_calibration"}
    result["model_digest"]=stable_digest(result)
    return result


def _pwl(value: float, knots: list[list[float]]) -> float:
    if not knots[0][0]<=value<=knots[-1][0]:
        raise ValueError("outside measured model domain")
    for a,b in zip(knots,knots[1:]):
        if value<=b[0]: return a[1]+(value-a[0])*(b[1]-a[1])/(b[0]-a[0])
    raise ValueError("missing interpolation interval")


def predict_surface(model: Mapping[str, Any], *, supply_v: float, load_ohm: float) -> float:
    load=finite(load_ohm,"load")
    if load<=0:raise ValueError("load must be positive")
    return _pwl(finite(supply_v,"supply"),model["supply_knots"])+_pwl(1/load,model["conductance_delta_knots"])


def render_surface(model: Mapping[str, Any]) -> str:
    module=identifier(model["module"]);roles=model["roles"]
    output=identifier(roles["output"]); supply=identifier(roles["supply"]); ground=identifier(roles["reference"])
    if "LOAD_OHM" in model["ports"]:raise ValueError("reserved parameter collides with port")
    ports=[("output var real " if p==output else "input real ")+identifier(p) for p in model["ports"]]
    lines=["// Measured DC candidate; static domain only; no hidden data used.",
           "module %s #(parameter real LOAD_OHM = %.17g) (%s);" % (module,model["reference_load_ohm"],", ".join(ports)),
           "timeunit 1ns; timeprecision 1ps;"]
    for function,knots in (("supply_curve",model["supply_knots"]),("load_delta",model["conductance_delta_knots"])):
        lines += ["function automatic real %s(input real x);" % function]
        for index,(a,b) in enumerate(zip(knots,knots[1:])):
            lines.append("  %s (x <= %.17g) %s = %.17g + (x - %.17g) * %.17g;" %
                         ("if" if index==0 else "else if",b[0],function,a[1],a[0],(b[1]-a[1])/(b[0]-a[0])))
        lines += ["  else %s = %.17g;" % (function,knots[-1][1]),"endfunction"]
    lo,hi=model["supply_knots"][0][0],model["supply_knots"][-1][0]
    guards=["%s != 0.0" % ground,"%s < %.17g"%(supply,lo),"%s > %.17g"%(supply,hi),
            "LOAD_OHM < %.17g"%model["load_range_ohm"][0],"LOAD_OHM > %.17g"%model["load_range_ohm"][1]]
    guards += ["%s != %.17g"%(identifier(p),v) for p,v in model["fixed_controls_v"].items()]
    sensitivity=" or ".join(identifier(p) for p in model["ports"] if p!=output)
    lines += ["always @(%s) begin"%sensitivity,
              '  if (%s) $fatal(1, "OUTSIDE_MEASURED_DC_DOMAIN");'%" || ".join(guards),
              "  else %s = supply_curve(%s) + load_delta(1.0/LOAD_OHM);"%(output,supply),
              "end","endmodule"]
    return "\n".join(lines)+"\n"


def render_dc_testbench(model: Mapping[str, Any], cases: list[dict], output_name: str="rnm.csv") -> str:
    if output_name!="rnm.csv":raise ValueError("unsupported testbench output name")
    if not cases or len(cases)>256:raise ValueError("testbench case count outside budget")
    roles=model["roles"]; output=roles["output"]
    lines=["module measured_dc_tb;","timeunit 1ns; timeprecision 1ps;","integer fd;"]
    for index,case in enumerate(cases):
        suffix="%03d"%index
        for pin in model["ports"]:
            lines.append("real "+pin+"_"+suffix+";")
        connections=", ".join(".%s(%s_%s)"%(pin,pin,suffix) for pin in model["ports"])
        lines.append("%s #(.LOAD_OHM(%.17g)) dut_%s(%s);"%(model["module"],case["load_ohm"],suffix,connections))
    lines += ["initial begin",'fd=$fopen("rnm.csv","w");', 'if (!fd) $fatal(1,"CSV_OPEN_FAILED");', '$fwrite(fd,"case,VOUT[V]\\n");']
    for index,case in enumerate(cases):
        for pin in model["ports"]:
            if pin!=output:lines.append("%s_%03d = %.17g;"%(pin,index,case["voltages"][pin]["dc_v"]))
    lines.append("#10;")
    for index,case in enumerate(cases):
        identifier(case["id"])
        lines.append('$fwrite(fd,"%s,%%.17g\\n",%s_%03d);'%(case["id"],output,index))
    lines += ['$fclose(fd); $display("AIVW_MEASURED_DC_COMPLETE"); $finish;',"end","endmodule"]
    return "\n".join(lines)+"\n"
