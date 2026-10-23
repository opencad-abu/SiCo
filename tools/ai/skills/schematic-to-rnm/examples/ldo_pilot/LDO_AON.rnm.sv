import cds_rnm_pkg::*;
`timescale 1ns/1ps

//
// LDO_AON real-number model.
// Interface from amsLDO/LDO_AON netlist ports (VDD VSS EN VOUT).
// Behavior from LDO_AON.spec.json: VOUT = VDD/2 while enabled and supply in window.
//
module LDO_AON (VDD, VSS, EN, VOUT);

   inout  VDD, VSS;
   input  EN;
   output VOUT;

   wreal1driver VDD, VSS;
   wreal1driver VOUT;
   logic        EN;
   real         VOUT_int;

   parameter real VDD_MAX = 2.9;
   parameter real VDD_MIN = 1.9;

   reg Supply;

   always @(VDD, VDD_MIN, VDD_MAX)
      Supply = (VDD > VDD_MAX || VDD < VDD_MIN) ? 1'b0 : 1'b1;

   always @(*)
      VOUT_int = Supply ? VDD / 2.0 : 0.0;

   assign VOUT = EN ? VOUT_int : 0.0;

endmodule
