# RNM Modeling Style

The target is a behavioral real-number model: continuous voltages on `wreal`
nets, behavior in `always`/`assign`, no transistors. This is the model Xcelium
elaborates in a mixed-signal run in place of the SPICE view. The reference point
is the RAK's pure-SystemVerilog LDO (`import cds_rnm_pkg::*;` + `wreal1driver`),
which compiles cleanly under `xrun -compile -sv`.

## Module shape

```systemverilog
import cds_rnm_pkg::*;
`timescale 1ns/1ps

module LDO_MASTER (VDD, VSS, VOUT);

   inout  VDD, VSS;          // interface matches the netlist ports exactly
   output VOUT;

   wreal1driver VDD, VSS;    // power/ground/analog -> wreal family
   wreal1driver VOUT;
   real         VOUT_int;    // real intermediates for the computation

   parameter real VDD_MAX = 5.5;   // spec metrics -> parameters
   parameter real VDD_MIN = 4.5;

   reg Supply;

   always @(VDD, VDD_MIN, VDD_MAX)
      Supply = (VDD > VDD_MAX || VDD < VDD_MIN) ? 1'b0 : 1'b1;

   always @(*)
      VOUT_int = Supply ? VDD / 2.0 : 0.0;

   assign VOUT = VOUT_int;

endmodule
```

## Rules

1. **Interface = netlist.** Port names, order, and direction come from the
   parsed subckt, not from the spec or the schematic drawing. Positional
   instantiation in the testbench depends on the order matching.
2. **Net types by port kind.**
   - `power` / `ground` / `analog` -> `wreal` (or `wreal1driver` when a single
     resolved driver is intended, or `wreal4state` when X/Z must propagate).
   - `digital` -> `logic` / `wire` / `reg`.
   - `wreal4state` requires `import cds_rnm_pkg::*;`.
3. **Behavior from the spec equation.** Compute into `real` intermediates, then
   drive the output port with one `assign` or `always` writer. Every output port
   must be driven.
4. **Boundary conditions.** Model what the spec states: supply window checks,
   enable gating, dropout (output follows input minus dropout near the rail),
   startup/shutdown delay. Keep them behavioral.
5. **No structure.** Never emit `nmos`/`pmos`/`cmos`/`tran`/`rtran` or
   per-transistor instances. Transistor sizes and layout-dependent parameters
   stay in the SPICE view the RNM replaces.
6. **Compile-clean in SV mode.** Prefer `import cds_rnm_pkg::*;` over
   `` `include "disciplines.vams" ``. The include forces Verilog-AMS mode and
   cannot be syntax-checked with plain `-sv`.
7. **`timescale`.** Include a `` `timescale `` directive.

## Idioms

Enable gating:

```systemverilog
assign VOUT = EN ? VOUT_int : 0.0;
```

Dropout near the rail:

```systemverilog
always @(*)
   if (!Supply)                 VOUT_int = 0.0;
   else if (VDD - VOUT_NOM < DROPOUT) VOUT_int = VDD - DROPOUT;
   else                          VOUT_int = VOUT_NOM;
```

Startup delay on the rising edge:

```systemverilog
always @(vout_target)
   if (vout_target > VOUT_int) VOUT_int = #(T_STARTUP*1us) vout_target;
   else                        VOUT_int = #(T_SHUTDOWN*1us) vout_target;
```

## What the checker enforces

- ports present, correctly directed, ordered to match the netlist;
- power/ground/analog ports are a wreal type, digital ports are not;
- at least one wreal port (it is a real-number model);
- an `always`/`assign` exists and every output is driven;
- no switch primitives;
- `wreal4state` implies the cds_rnm_pkg import;
- with `--compile`, the module passes `xrun -compile`.

A clean check means the interface is right and it compiles — not that the
behavior is correct. Confirm behavior against a golden model or an approved
simulation, reported separately.
