import cds_rnm_pkg::*;
`timescale 1ns/1ps

//
// LDO_MASTER real-number model.
// Interface from amsLDO/LDO_MASTER netlist ports (VDD VSS VOUT).
// Behavior from LDO_MASTER.spec.json: VOUT = VDD/2 while supply in window.
//
module LDO_MASTER (VDD, VSS, VOUT);

   inout  VDD, VSS;
   output VOUT;

   wreal1driver VDD, VSS;
   wreal1driver VOUT;
   real         VOUT_int;

   parameter real VDD_MAX = 5.5;
   parameter real VDD_MIN = 4.5;

   reg Supply;

   always @(VDD, VDD_MIN, VDD_MAX)
      Supply = (VDD > VDD_MAX || VDD < VDD_MIN) ? 1'b0 : 1'b1;

   always @(*)
      VOUT_int = Supply ? VDD / 2.0 : 0.0;

   assign VOUT = VOUT_int;

endmodule
