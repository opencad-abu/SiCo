"""SystemVerilog implementation of the identified model-class hypothesis."""

from __future__ import annotations

import math

from ..electrical_experiment import identifier


def render_model(model):
    out = model["roles"]["output"]
    ports = [("output var real " if p == out else "input real ")+identifier(p) for p in model["ports"]]
    dt = model["step_s"]
    lines = ["// Identified sampled state hypothesis; public data; qualification required.",
             "// load_conductance is an environment property, not an OA terminal.",
             "module %s (%s);" % (identifier(model["module"]), ", ".join(ports)),
             "timeunit 1ns; timeprecision 1ps;",
             "real load_conductance; real supply_current_a; real prev[%d];" % len(model["axes"]),
             "real x[%d]; real phi[%d]; real goal_v, goal_i, slew; integer j;" %
             (len(model["axes"]), len(model["channels"]["output"]["coefficients"]))]
    if "memory" in model:
        lines += ["real base_v, base_i; real memory_phi[%d]; real memory_state[%d];" %
                  (len(model["axes"])*12, len(model["memory"]["coefficients"]["output"]))]
    if "discharge" in model:
        lines += ["real discharge_previous, discharge_lograte, discharge_base;"]
    lines += ["function automatic real hat(input real x, a, b, c, input integer first, last);",
              "if(x == b) hat=1.0;",
              "else if(!first && x>a && x<b) hat=(x-a)/(b-a);",
              "else if(!last && x>b && x<c) hat=(c-x)/(c-b);",
              "else hat=0.0; endfunction",
              "task automatic advance(input integer initialize); begin"]
    for i, axis in enumerate(model["axes"]):
        expr = "load_conductance" if i == len(model["axes"])-1 else identifier(axis["name"])
        lines += ["x[%d]=%s;" % (i, expr),
                  'if(x[%d] < %.17g || x[%d] > %.17g) $fatal(1,"OUTSIDE_IDENTIFIED_DOMAIN");' %
                  (i, axis["knots"][0]-1e-12, i, axis["knots"][-1]+1e-12)]
        lines += ["if(x[%d]<%.17g) x[%d]=%.17g;" % (i, axis["knots"][0], i, axis["knots"][0]),
                  "if(x[%d]>%.17g) x[%d]=%.17g;" % (i, axis["knots"][-1], i, axis["knots"][-1])]
    lines += ['if(%s != 0.0) $fatal(1,"REFERENCE_DOMAIN");' % identifier(model["roles"]["reference"]),
              "phi[0]=1.0;"]
    feature = 1
    for i, axis in enumerate(model["axes"]):
        knots = axis["knots"]
        for j, knot in enumerate(knots[:-1]):
            lines.append("phi[%d]=hat(x[%d],%.17g,%.17g,%.17g,%d,0);" %
                         (feature, i, knots[max(0, j-1)], knot, knots[j+1], int(j == 0)))
            feature += 1
    for i, axis in enumerate(model["axes"]):
        lines.append("slew=initialize ? 0.0 : (x[%d]-prev[%d])/%.17g;" % (i, i, dt*axis["slew_scale_per_s"]))
        mode = model["identification_options"].get("slew_features", "global")
        if mode == "signed_polynomial":
            knots = axis["knots"]
            for direction in (1., -1.):
                for power in (.5, 1., 2.):
                    amplitude = "((%.1f*slew)>0.0 ? (%.1f*slew)**%.1f : 0.0)" % (direction, direction, power)
                    for j, knot in enumerate(knots):
                        lines.append("phi[%d]=%s*hat(x[%d],%.17g,%.17g,%.17g,%d,%d);" %
                                     (feature, amplitude, i, knots[max(0, j-1)], knot, knots[min(len(knots)-1, j+1)], int(j == 0), int(j == len(knots)-1)))
                        feature += 1
        elif mode == "local":
            knots = axis["knots"]
            for j, knot in enumerate(knots):
                lines.append("phi[%d]=slew*hat(x[%d],%.17g,%.17g,%.17g,%d,%d);" %
                             (feature, i, knots[max(0, j-1)], knot, knots[min(len(knots)-1, j+1)], int(j == 0), int(j == len(knots)-1)))
                feature += 1
        else:
            lines.append("phi[%d]=slew;" % feature)
            feature += 1
    for name, goal in (("output", "goal_v"), ("current", "goal_i")):
        coeff = model["channels"][name]["coefficients"]
        lines.append(goal+"="+" + ".join("(%.17g)*phi[%d]" % (c, i) for i, c in enumerate(coeff))+";")
        state = out if name == "output" else "supply_current_a"
        target = state
        if "memory" in model:
            state = "base_v" if name == "output" else "base_i"
        elif "discharge" in model and name == "output":
            state = "discharge_base"
        tau = model["channels"][name]["tau_s"]
        a = math.exp(-dt/tau) if tau else 0.
        lines.append("%s=initialize ? %s : %.17g*%s + %.17g*%s;" % (state, goal, a, state, 1-a, goal))
        if "memory" in model or ("discharge" in model and name == "output"):
            lines.append("%s=%s;" % (target, state))
    if "memory" in model:
        from .state_memory import render_memory
        lines += render_memory(model)
    if "discharge" in model:
        from .discharge_state import render_discharge
        lines += render_discharge(model)
    lines += ["for(j=0;j<%d;j=j+1) prev[j]=x[j];" % len(model["axes"]),
              "end endtask", "endmodule"]
    return "\n".join(lines)+"\n"


def _function(name, points):
    lines = ["function automatic real %s(input real t);" % name,
             "if(t<=%.17g) %s=%.17g;" % (points[0][0], name, points[0][1])]
    for a, b in zip(points, points[1:]):
        lines.append("else if(t<=%.17g) %s=%.17g + (t-%.17g)*%.17g;" %
                     (b[0], name, a[1], a[0], (b[1]-a[1])/(b[0]-a[0])))
    lines += ["else %s=%.17g;" % (name, points[-1][1]), "endfunction"]
    return lines


def render_testbench(model, plan, case):
    out = plan["roles"]["output"]
    stop, dt = plan["analysis"]["stop_s"], model["step_s"]
    ticks = dt*1e9
    if not math.isclose(round(dt/1e-12)*1e-12, dt, rel_tol=1e-12):
        raise ValueError("model step is not representable by Xcelium precision")
    lines = ["module trajectory_tb;", "timeunit 1ns; timeprecision 1ps;", "integer fd,k; real t;"]
    lines += ["real %s;" % identifier(p) for p in model["ports"]]
    lines += ["%s dut(%s);" % (identifier(model["module"]), ", ".join(".%s(%s)" % (p, p) for p in model["ports"]))]
    for pin, source in case["voltages"].items():
        points = source.get("pwl", [[0., source.get("dc_v")], [stop, source.get("dc_v")]])
        lines += _function("drive_"+identifier(pin), points)
    load = case["load_conductance_pwl_s"] or [[0., 1/case["load_ohm"]], [stop, 1/case["load_ohm"]]]
    lines += _function("drive_load", load)
    lines += ["initial begin", 'fd=$fopen("waveforms.csv","w");', 'if(!fd) $fatal(1,"CSV_OPEN_FAILED");',
              '$fwrite(fd,"time[s],%s[V],VPROBE:p[A]\\n");' % out,
              "for(k=0;k<=%d;k=k+1) begin" % round(stop/dt), "t=k*%.17g;" % dt]
    lines += ["%s=drive_%s(t);" % (pin, pin) for pin in case["voltages"]]
    lines += ["dut.load_conductance=drive_load(t);", "#0; dut.advance(k==0); #0;",
              '$fwrite(fd,"%%.17g,%%.17g,%%.17g\\n",t,%s,dut.supply_current_a);' % out,
              "#(%.17g); end" % ticks,
              '$fclose(fd); $display("AIVW_TRAJECTORY_COMPLETE"); $finish; end', "endmodule"]
    return "\n".join(lines)+"\n"
