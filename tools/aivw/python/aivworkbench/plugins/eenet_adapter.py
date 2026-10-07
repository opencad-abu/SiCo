"""Cadence EEnet adapter for an explicitly bounded sampled model ABI.

Grounded passive resistive loads only. This adapter supplies scheduling and
terminal current balance; it does not qualify the underlying block model or
make capacitive/active/reverse-current network behavior supported.
"""

from ..electrical_experiment import identifier


def render_adapter(model):
    name = identifier(model["module"])
    roles = model["roles"]
    supply, ground, output = [identifier(roles[k]) for k in ("supply", "reference", "output")]
    control = [identifier(p) for p in roles["controls"]]
    axes = model["axes"]
    load = axes[-1]
    if load["name"] != "load_conductance":
        raise ValueError("adapter requires an identified load axis")
    pins = ["inout EE_pkg::EEnet "+p for p in (supply, ground, output)]
    pins += ["input real "+p for p in control]
    lines = ["import EE_pkg::*;", "module %s_eenet(%s);" % (name, ",".join(pins)),
             "timeunit 1ns; timeprecision 1ps;",
             "real supply_v, ground_v, output_v; real drive_v=0.0, draw_i=0.0;",
             "real load_g, external_i; integer initialized=0;",
             "localparam real DRIVER_R=1.0;",
             "assign %s = '{drive_v, 0.0, DRIVER_R};" % output,
             "assign %s = '{0.0, -draw_i, `wrealZState};" % supply,
             "assign %s = '{0.0, draw_i-(drive_v-%s.V)/DRIVER_R, `wrealZState};" % (ground, output),
             "%s core(%s);" % (name, ",".join(".%s(%s)" % (p,
                 "supply_v" if p == supply else "ground_v" if p == ground else "output_v" if p == output else p)
                 for p in model["ports"])),
             "initial begin", "#(1step);", "forever begin",
             'if(%s.V !== 0.0) $fatal(1,"EENET_REQUIRES_ZERO_REFERENCE");' % ground,
             'if(%s.R === `wrealXState || %s.R === `wrealZState || %s.R <= 0.0) $fatal(1,"EENET_OUTPUT_DRIVER_CONFLICT");' % (output, output, output),
             "load_g=1.0/%s.R-1.0/DRIVER_R;" % output,
             'if(load_g < %.17g || load_g > %.17g) $fatal(1,"EENET_LOAD_OUTSIDE_DOMAIN");' %
             (load["knots"][0]-1e-12, load["knots"][-1]+1e-12),
             "external_i=%s.V*(load_g+1.0/DRIVER_R)-drive_v/DRIVER_R;" % output,
             'if(external_i > 1e-9 || external_i < -1e-9) $fatal(1,"EENET_ACTIVE_LOAD_UNSUPPORTED");',
             "supply_v=%s.V; ground_v=%s.V; core.load_conductance=load_g;" % (supply, ground),
             "#0; core.advance(!initialized); #0;",
             "drive_v=output_v*(1.0+load_g*DRIVER_R);",
             "draw_i=core.supply_current_a; initialized=1;",
             "#(%.17g);" % (model["step_s"]*1e9), "end end", "endmodule"]
    return "\n".join(lines)+"\n"


def render_network_testbench(model, nominal_supply, *, invalid_load=False, negative_case=None):
    name = identifier(model["module"])
    roles = model["roles"]
    control = [identifier(p) for p in roles["controls"]]
    lines = ["import EE_pkg::*;", "module eenet_tb;", "timeunit 1ns; timeprecision 1ps;",
             "EEnet rail, ground, out; real load_r=20000.0; real source_v=%.17g;" % nominal_supply,
             "localparam real SOURCE_R=10.0; integer fd; real source_i, load_i, balance;",
             "assign rail='{source_v,0.0,SOURCE_R}; assign ground='{0.0,0.0,0.0};",
             "assign out='{0.0,0.0,load_r};"]
    if negative_case == "active_load":
        lines += ["real active_i=0.0; assign out='{0.0, active_i, `wrealZState};",
                  "initial begin #(10000); active_i=0.001; end"]
    if negative_case == "ideal_driver":
        lines += ["real conflict_r=`wrealZState; assign out='{1.0, 0.0, conflict_r};",
                  "initial begin #(10000); conflict_r=0.0; end"]
    lines += ["real %s=%.17g;" % (p, nominal_supply) for p in control]
    mapping = {roles["supply"]: "rail", roles["reference"]: "ground", roles["output"]: "out"}
    mapping.update({p: p for p in control})
    lines += ["%s_eenet dut(%s);" % (name, ",".join(".%s(%s)" % item for item in mapping.items())),
              "initial begin", '#(10000); load_r=%.17g;' % (100. if invalid_load else 5000.),
              "#(10000); load_r=10000.0; end",
              "initial begin", 'fd=$fopen("network.csv","w");',
              '$fwrite(fd,"time[s],rail[V],out[V],source[A],load[A],ground[A],kcl[A]\\n");',
              "repeat(300) begin #(100); #(1step);",
              "source_i=(source_v-rail.V)/SOURCE_R; load_i=out.V/load_r;",
              "balance=source_i-load_i-ground.I;",
              '$fwrite(fd,"%.17g,%.17g,%.17g,%.17g,%.17g,%.17g,%.17g\\n",$realtime*1e-9,rail.V,out.V,source_i,load_i,ground.I,balance);',
              'if(balance>1e-9 || balance< -1e-9) $fatal(1,"NETWORK_KCL_FAILED");',
              'if(source_i<0.0 || rail.V>=source_v) $fatal(1,"SUPPLY_CURRENT_NOT_COUPLED");',
              "end", '$fclose(fd); $display("AIVW_EENET_ADAPTER_COMPLETE"); $finish;', "end endmodule"]
    return "\n".join(lines)+"\n"
