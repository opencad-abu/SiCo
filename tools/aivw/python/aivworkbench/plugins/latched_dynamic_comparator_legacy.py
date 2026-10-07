"""Legacy smoke testbench rendering for the latched comparator."""

from __future__ import annotations

from typing import Any, Mapping

from .latched_dynamic_comparator_model import _sv_name


def _render_legacy_smoke_testbench(spec: Mapping[str, Any]) -> str:
    roles = spec["interface"]["roles"]
    module = _sv_name(str(spec["target"]["module"]))
    positive_input = _sv_name(str(roles["positive_input"]))
    negative_input = _sv_name(str(roles["negative_input"]))
    positive_output = _sv_name(str(roles["positive_output"]))
    negative_output = _sv_name(str(roles["negative_output"]))
    clock = _sv_name(str(roles["clock"]))
    connections = ", ".join(
        (
            f".{positive_output} (out_p)",
            f".{negative_output} (out_n)",
            f".{positive_input} (in_p)",
            f".{negative_input} (in_n)",
            f".{clock} (clk_drive)",
        )
    )
    return f'''`timescale 1ns/1ps
module aivw_latched_comparator_smoke_tb;
   real in_p_drive, in_n_drive;
   wreal1driver in_p, in_n;
   wreal1driver out_p, out_n;
   logic clk_drive;
   real tolerance;
   {module} dut({connections});
   assign in_p = in_p_drive;
   assign in_n = in_n_drive;
   initial begin
      tolerance = 1.0e-9;
      clk_drive = 1'b0; in_p_drive = 0.0; in_n_drive = 0.0;
      #1;
      if ((out_p > tolerance) || (out_n < 1.2-tolerance)) $fatal(1, "initial state mismatch");
      in_p_drive = 0.2; in_n_drive = 0.0; #1;
      if ((out_p > tolerance) || (out_n < 1.2-tolerance)) $fatal(1, "clock hold mismatch");
      clk_drive = 1'b1; #1; clk_drive = 1'b0;
      if ((out_p < 1.2-tolerance) || (out_n > tolerance)) $fatal(1, "positive decision mismatch");
      in_p_drive = 0.0; in_n_drive = 0.2; #1; clk_drive = 1'b1; #1; clk_drive = 1'b0;
      if ((out_p > tolerance) || (out_n < 1.2-tolerance)) $fatal(1, "negative decision mismatch");
      in_p_drive = 0.0; in_n_drive = 0.0; #1; clk_drive = 1'b1; #1; clk_drive = 1'b0;
      if ((out_p < 0.6-tolerance) || (out_p > 0.6+tolerance) ||
          (out_n < 0.6-tolerance) || (out_n > 0.6+tolerance)) $fatal(1, "tie decision mismatch");
      $display("AIVW_RNM_SMOKE checks=5 PASS");
      $finish;
   end
endmodule
'''


__all__ = ["_render_legacy_smoke_testbench"]
